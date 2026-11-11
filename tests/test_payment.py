import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import stripe

import marche
from marche import create_app
from marche.extensions import db
from marche.models import OrderStatus
from marche.services import payment
from marche.services.payment import SESSION_EXPIRES_MINUTES, PaymentError

# conftest の block_real_stripe が差し替える前の、本物の _client
REAL_CLIENT = payment._client

APP_DIR = Path(marche.__file__).parent


class FakeSessions:
    """stripe.StripeClient の v1.checkout.sessions の代わり。渡された引数を記録する"""

    def __init__(self):
        self.created = []
        self.retrieved = []
        self.expired = []
        self.error = None  # 入れておくと、どのメソッドもそれを投げる
        self.expire_error = None  # 入れておくと、expire だけがそれを投げる
        self.status = (
            "open"  # retrieve が返す Session の状態（open / expired / complete）
        )
        self.url = "https://checkout.stripe.test/pay/cs_test_abc"

    def create(self, params):
        if self.error:
            raise self.error
        self.created.append(params)
        return SimpleNamespace(id="cs_test_abc", url=self.url)

    def retrieve(self, session_id):
        if self.error:
            raise self.error
        self.retrieved.append(session_id)
        return SimpleNamespace(id=session_id, payment_status="paid", status=self.status)

    def expire(self, session_id):
        if self.error:
            raise self.error
        if self.expire_error:
            raise self.expire_error
        self.expired.append(session_id)


@pytest.fixture
def sessions(monkeypatch):
    """payment._client を、フェイクのクライアントを返す関数に差し替える"""
    fake = FakeSessions()
    client = SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=fake))
    )
    monkeypatch.setattr(payment, "_client", lambda: client)
    return fake


@pytest.fixture
def order(customer, producer, make_product, make_order):
    carrot = make_product(producer, name="にんじん", price=200, stock=10)
    pork = make_product(producer, name="ポーク", price=1200, stock=5)
    return make_order(customer, [(carrot, 3), (pork, 2)])


def create_session(app, order):
    with app.test_request_context():
        return payment.create_checkout_session(order)


# --- create_checkout_session ---


def test_決済ページのIDとURLを返す(app, order, sessions):
    assert create_session(app, order) == (
        "cs_test_abc",
        "https://checkout.stripe.test/pay/cs_test_abc",
    )


def test_line_itemsは注文の明細から作られ金額は円のまま(app, order, sessions):
    create_session(app, order)
    (params,) = sessions.created
    assert params["mode"] == "payment"
    items = [
        (
            line["quantity"],
            line["price_data"]["currency"],
            line["price_data"]["unit_amount"],
            line["price_data"]["product_data"]["name"],
        )
        for line in params["line_items"]
    ]
    # unit_amount は明細の単価（円）のまま。100倍しない
    assert items == [(3, "jpy", 200, "にんじん"), (2, "jpy", 1200, "ポーク")]
    total = sum(
        line["quantity"] * line["price_data"]["unit_amount"]
        for line in params["line_items"]
    )
    assert total == order.total_amount == 3000


def test_注文のあとに商品の価格を変えても金額は注文時点のまま(app, order, sessions):
    for item in order.items:
        item.product.price = 999
        item.product.name = "あとで変えた名前"
    marche.extensions.db.session.commit()
    create_session(app, order)
    (params,) = sessions.created
    amounts = [line["price_data"]["unit_amount"] for line in params["line_items"]]
    names = [
        line["price_data"]["product_data"]["name"] for line in params["line_items"]
    ]
    assert amounts == [200, 1200]
    assert names == ["にんじん", "ポーク"]


def test_注文ID_メール_戻り先がセットされる(app, order, customer, sessions):
    create_session(app, order)
    (params,) = sessions.created
    assert params["metadata"] == {"order_id": str(order.id)}
    assert params["customer_email"] == customer.email
    # {CHECKOUT_SESSION_ID} は Stripe が置き換えるので、エンコードされていてはいけない
    assert params["success_url"] == (
        f"http://localhost/orders/{order.id}/complete?session_id={{CHECKOUT_SESSION_ID}}"
    )
    assert params["cancel_url"] == f"http://localhost/orders/{order.id}/cancel"


def test_決済ページの期限は31分後(app, order, sessions):
    assert SESSION_EXPIRES_MINUTES == 31  # Stripe の最短（30分）より少し長い
    before = int(time.time())
    create_session(app, order)
    after = int(time.time())
    (params,) = sessions.created
    assert before + 31 * 60 <= params["expires_at"] <= after + 31 * 60


def test_URLが無い応答はPaymentError(app, order, sessions):
    sessions.url = None
    with pytest.raises(PaymentError):
        create_session(app, order)


# --- retrieve_session / expire_session ---


def test_Sessionを取得できる(app, sessions):
    session = payment.retrieve_session("cs_test_abc")
    assert session.payment_status == "paid"
    assert sessions.retrieved == ["cs_test_abc"]


def test_Sessionを期限切れにできる(app, sessions):
    assert payment.expire_session("cs_test_abc") is None
    assert sessions.expired == ["cs_test_abc"]
    assert sessions.retrieved == []  # 成功したときは、状態を確かめない


# Stripe は、有効（open）でない Session を期限切れにしようとするとエラーを返す


def test_すでに期限切れのSessionは成功として扱う(app, sessions):
    sessions.expire_error = stripe.InvalidRequestError("expired", "session")
    sessions.status = "expired"
    assert payment.expire_session("cs_test_abc") is None
    assert sessions.retrieved == ["cs_test_abc"]  # 理由を確かめた


@pytest.mark.parametrize("status", ["complete", "open"])
def test_期限切れにできず期限切れでもないSessionはPaymentError(app, sessions, status):
    # 支払い済み（complete）の Session は、取り消してはいけない
    sessions.expire_error = stripe.InvalidRequestError("cannot expire", "session")
    sessions.status = status
    with pytest.raises(PaymentError) as error:
        payment.expire_session("cs_test_abc")
    assert "注文はキャンセルしていません" in str(error.value)
    assert isinstance(error.value.__cause__, stripe.StripeError)


def test_期限切れにできず状態も確かめられないとPaymentError(app, sessions, caplog):
    sessions.expire_error = stripe.InvalidRequestError("詳細: sk_test_leak", "session")

    def retrieve_fails(session_id):
        raise stripe.APIConnectionError("接続できません: sk_test_leak")

    sessions.retrieve = retrieve_fails
    with pytest.raises(PaymentError) as error:
        payment.expire_session("cs_test_abc")
    assert "注文はキャンセルしていません" in str(error.value)
    assert "sk_test_leak" not in str(error.value)
    assert "sk_test_leak" not in caplog.text  # 状態の確認が失敗したことは、ログに残る
    assert "決済ページの状態の確認" in caplog.text


def test_通信の失敗で期限切れにできないときは状態を確かめて判断する(app, sessions):
    sessions.expire_error = stripe.APIConnectionError("接続できません")
    sessions.status = "open"
    with pytest.raises(PaymentError):
        payment.expire_session("cs_test_abc")
    assert sessions.retrieved == ["cs_test_abc"]


# --- StripeError は PaymentError にする ---


@pytest.mark.parametrize(
    "call",
    [
        lambda order: payment.create_checkout_session(order),
        lambda order: payment.retrieve_session("cs_test_abc"),
        lambda order: payment.expire_session("cs_test_abc"),
    ],
    ids=["create", "retrieve", "expire"],
)
def test_StripeErrorはPaymentErrorになる(app, order, sessions, caplog, call):
    sessions.error = stripe.StripeError("詳細: sk_test_leak")
    with app.test_request_context(), pytest.raises(PaymentError) as error:
        call(order)
    message = str(error.value)
    assert re.search(r"[ぁ-んァ-ヶ一-龥]", message)  # 画面に出せる日本語
    # Stripe のメッセージは、画面にもログにも出さない
    assert "sk_test_leak" not in message
    assert "sk_test_leak" not in caplog.text
    assert isinstance(error.value.__cause__, stripe.StripeError)


# --- キーとクライアント ---


def test_キーが無いとPaymentError(app):
    app.config["STRIPE_SECRET_KEY"] = ""
    with pytest.raises(PaymentError) as error:
        REAL_CLIENT()
    assert "決済の設定がされていません" in str(error.value)


def test_キーがあればStripeClientを作る_全体の設定は書き換えない(app):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_dummy"
    client = REAL_CLIENT()  # 作るだけで、通信はしない
    assert isinstance(client, stripe.StripeClient)
    assert stripe.api_key is None  # stripe.api_key = ... は使わない


# --- Stripe を使う場所と、差し替えの仕組み ---


def python_files():
    return sorted(APP_DIR.rglob("*.py"))


def test_stripeを使うのはservices_payment_pyだけ():
    users = [
        path.relative_to(APP_DIR).as_posix()
        for path in python_files()
        if re.search(r"^\s*(import stripe|from stripe)", path.read_text(), re.MULTILINE)
    ]
    # Webhook の署名の検証だけは、次の章で views/webhooks.py に書く
    assert users == ["services/payment.py"]


def test_stripe_api_keyを書き換えていない():
    offenders = [
        path.relative_to(APP_DIR).as_posix()
        for path in python_files()
        if re.search(r"stripe\.api_key\s*=", path.read_text())
    ]
    assert offenders == []


def test_テストではextensionsにフェイクが入る(app, fake_payment):
    assert app.extensions["payment"] is fake_payment


def test_PAYMENT_SERVICEが無ければ本物のモジュールが入る():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert app.extensions["payment"] is payment


def test_本物のStripeを呼ぼうとするとテストが失敗する(app):
    # conftest の block_real_stripe が効いていること
    with pytest.raises(AssertionError, match="本物の Stripe"):
        payment.expire_session("cs_test_abc")


# --- 画面から通す（画面は本物の payment.py を呼び、_client だけをフェイクにする） ---


@pytest.fixture
def real_payment(app, sessions):
    app.extensions["payment"] = payment
    return payment


def pending_order_with_session(order):
    order.stripe_checkout_session_id = "cs_test_abc"
    db.session.commit()
    return order


def test_期限切れのSessionの注文も取り消せる(
    client, customer, order, login, sessions, real_payment
):
    pending_order_with_session(order)
    sessions.expire_error = stripe.InvalidRequestError("expired", "session")
    sessions.status = "expired"  # 31分たって、Stripe 側ですでに期限切れ
    login(customer)
    response = client.post(f"/orders/{order.id}/cancel")
    assert response.status_code == 302
    db.session.refresh(order)
    assert order.status == OrderStatus.CANCELLED
    assert "注文をキャンセルしました" in client.get(response.headers["Location"]).text


def test_支払い済みのSessionの注文は取り消されない(
    client, customer, order, login, sessions, real_payment
):
    pending_order_with_session(order)
    sessions.expire_error = stripe.InvalidRequestError("cannot expire", "session")
    sessions.status = "complete"  # すでに支払われた
    login(customer)
    response = client.post(f"/orders/{order.id}/cancel")
    assert response.status_code == 302
    db.session.refresh(order)
    assert order.status == OrderStatus.PENDING  # 取り消さない
    assert (
        "注文はキャンセルしていません" in client.get(response.headers["Location"]).text
    )
