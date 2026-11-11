from datetime import datetime

import pytest
from flask import g

from marche.extensions import db
from marche.models import JST, Order, OrderItem, OrderStatus
from marche.services.inventory import available_stock
from marche.services.payment import PaymentError


def jst(*args):
    """日本時間の日時を、DB に保存するときと同じ tzinfo なしの形で作る（now_jst と同じ）"""
    return datetime(*args, tzinfo=JST).replace(tzinfo=None)


@pytest.fixture
def products(producer, make_product):
    carrot = make_product(
        producer, name="にんじん", price=200, stock=10, is_published=True
    )
    pork = make_product(producer, name="ポーク", price=1200, stock=5, is_published=True)
    return carrot, pork


def all_orders():
    return db.session.scalars(db.select(Order)).all()


# --- 日付の表示 ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (jst(2026, 11, 14, 10, 0), "11月14日(土) 10:00"),
        (jst(2026, 11, 10, 20, 35), "11月10日(火) 20:35"),
        (jst(2026, 11, 15, 9, 5), "11月15日(日) 09:05"),
        (jst(2026, 11, 16, 0, 0), "11月16日(月) 00:00"),
        (jst(2026, 12, 1, 23, 59), "12月1日(火) 23:59"),
    ],
)
def test_日時は日本の形で表示される(app, value, expected):
    assert app.jinja_env.filters["datetime_jp"](value) == expected


# --- POST /orders/（注文を作る） ---


def test_カートから注文できる(
    client, customer, products, login, set_cart, read_cart, fake_payment
):
    carrot, pork = products
    login(customer)
    set_cart({str(carrot.id): 3, str(pork.id): 2})
    response = client.post("/orders/")
    (order,) = all_orders()
    # 決済ページ（Stripe。ここではフェイク）へ 303 で移る
    assert response.status_code == 303
    assert response.headers["Location"] == (
        f"https://checkout.stripe.test/pay/cs_test_fake_{order.id}"
    )
    assert fake_payment.created == [order.id]
    assert order.stripe_checkout_session_id == f"cs_test_fake_{order.id}"
    assert order.user_id == customer.id
    assert order.status == OrderStatus.PENDING  # 支払いが確定するまで pending
    assert order.total_amount == 200 * 3 + 1200 * 2  # DB の価格 × 数量
    assert len(order.items) == 2
    assert read_cart() is None  # カートは空になる


def test_フォームに金額を混ぜても無視される(
    client, customer, products, login, set_cart
):
    carrot, _ = products
    login(customer)
    set_cart({str(carrot.id): 3})
    client.post(
        "/orders/",
        data={
            "price": "1",
            "unit_price": "1",
            "total_amount": "1",
            "subtotal": "1",
            "quantity": "99",
            "product_id": str(carrot.id),
            "user_id": "999",
        },
    )
    (order,) = all_orders()
    assert order.total_amount == 600
    assert order.user_id == customer.id
    assert order.items[0].quantity == 3
    assert order.items[0].unit_price == 200


def test_注文を作っても在庫は減らず買える数だけ減る(
    client, customer, products, login, set_cart
):
    carrot, _ = products
    login(customer)
    set_cart({str(carrot.id): 3})
    client.post("/orders/")
    db.session.refresh(carrot)
    assert carrot.stock == 10
    assert "在庫 7" in client.get("/").text


def test_生産者も注文できる(client, other_producer, products, login, set_cart):
    carrot, _ = products
    login(other_producer)
    set_cart({str(carrot.id): 1})
    response = client.post("/orders/")
    assert response.status_code == 303
    (order,) = all_orders()
    assert order.user_id == other_producer.id


def test_在庫を超えると注文されずカートに戻る(
    client, customer, products, login, set_cart, read_cart
):
    _, pork = products  # 在庫 5
    login(customer)
    set_cart({str(pork.id): 6})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert all_orders() == []
    assert read_cart() == {str(pork.id): 6}  # カートはそのまま
    assert (
        "「ポーク」の在庫が足りません（いま買える数: 5）" in client.get("/cart/").text
    )


def test_非公開になった商品があると注文されずカートに戻る(
    client, customer, products, login, set_cart, read_cart
):
    carrot, pork = products
    login(customer)
    set_cart({str(carrot.id): 1, str(pork.id): 1})
    carrot.is_published = False
    db.session.commit()
    response = client.post("/orders/", follow_redirects=True)
    assert response.status_code == 200
    assert "購入できない商品がカートに入っています" in response.text
    assert all_orders() == []  # 一部だけの注文も作られない


def test_空のカートでは注文されない(client, customer, login):
    login(customer)
    response = client.post("/orders/", follow_redirects=True)
    assert "カートに商品がありません" in response.text
    assert all_orders() == []


def test_未ログインでは注文できずログイン画面へ移る(
    client, products, set_cart, read_cart
):
    carrot, _ = products
    set_cart({str(carrot.id): 1})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
    assert all_orders() == []
    assert read_cart() == {str(carrot.id): 1}  # カートは残る


def test_注文の作成はGETでは動かない(client, customer, login):
    login(customer)
    assert client.get("/orders/").status_code == 405


def test_CSRFトークンの無い注文のPOSTは400(app, customer, products, set_cart):
    carrot, _ = products
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["cart"] = {str(carrot.id): 1}
    assert client.post("/orders/").status_code == 400
    assert all_orders() == []


# --- GET /orders/<id>（注文の詳細） ---


def test_自分の注文の詳細を見られる(client, customer, products, make_order, login):
    carrot, pork = products
    order = make_order(
        customer, [(carrot, 3), (pork, 2)], created_at=jst(2026, 11, 14, 10, 0)
    )
    login(customer)
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 200
    text = response.text
    for expected in (
        f"注文 {order.id}",
        "11月14日(土) 10:00",
        "支払い待ち",
        "お支払いが完了していません",
        "にんじん",
        "200円",
        "600円",
        "ポーク",
        "1,200円",
        "2,400円",
        "3,000円",
    ):
        assert expected in text


@pytest.mark.parametrize(
    ("status", "label"),
    [(OrderStatus.PAID, "支払い済み"), (OrderStatus.CANCELLED, "キャンセル")],
)
def test_支払い待ち以外にはお支払いが完了していない案内が出ない(
    client, customer, products, make_order, login, status, label
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 1)], status=status)
    login(customer)
    text = client.get(f"/orders/{order.id}").text
    assert label in text
    assert "お支払いが完了していません" not in text


def test_他の購入者の注文は開けない(
    client, customer, other_customer, products, make_order, login
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 3)])
    login(other_customer)
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 403
    assert "にんじん" not in response.text  # 注文の中身は出ない
    assert "600円" not in response.text


def test_他の生産者の注文は開けない(
    client, customer, other_producer, products, make_order, login
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 3)])
    login(other_producer)
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_商品を出品した生産者でも他人の注文は開けない(
    client, customer, producer, products, make_order, login
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 3)])
    login(producer)
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_管理者は他人の注文を開ける(
    client, customer, admin, products, make_order, login
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 3)])
    login(admin)
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 200
    assert "600円" in response.text


def test_未ログインで注文の詳細を開くとログイン画面へ移る(
    client, customer, products, make_order
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 1)])
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


@pytest.mark.parametrize("path", ["/orders/9999", "/orders/abc", "/orders/-1"])
def test_存在しない注文は404(client, customer, login, path):
    login(customer)
    assert client.get(path).status_code == 404


def test_商品を変更しても注文の表示は変わらない(
    client, customer, products, make_order, login
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 3)])
    carrot.name = "あとで変えた名前"
    carrot.price = 999
    db.session.commit()
    login(customer)
    text = client.get(f"/orders/{order.id}").text
    assert "にんじん" in text
    assert "200円" in text
    assert "600円" in text
    assert "あとで変えた名前" not in text
    assert "999円" not in text


def test_注文の商品名はエスケープされる(
    client, customer, producer, make_product, make_order, login
):
    product = make_product(
        producer, name="<script>alert(1)</script>", stock=10, is_published=True
    )
    order = make_order(customer, [(product, 1)])
    login(customer)
    text = client.get(f"/orders/{order.id}").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


# --- 流れ全体 ---


def test_カートに入れて注文して詳細を見るまで(client, customer, products, login):
    carrot, pork = products
    login(customer)
    client.post(f"/cart/add/{carrot.id}", data={"quantity": "3"})
    client.post(f"/cart/add/{pork.id}", data={"quantity": "2"})
    cart = client.get("/cart/").text
    assert "3,000円" in cart
    assert "注文して支払いへ進む" in cart

    # 注文すると決済ページ（フェイク）へ移る。リダイレクトはたどらない
    response = client.post("/orders/")
    assert response.status_code == 303
    assert response.headers["Location"].startswith("https://checkout.stripe.test/")
    assert "カートは空です" in client.get("/cart/").text

    (order,) = all_orders()
    detail = client.get(f"/orders/{order.id}").text
    assert "支払い待ち" in detail
    assert "3,000円" in detail
    assert [(i.product_name, i.quantity) for i in order.items] == [
        ("にんじん", 3),
        ("ポーク", 2),
    ]
    assert len(db.session.scalars(db.select(OrderItem)).all()) == 2


def test_セッションの数量が上限を超えていても注文されない(
    client, customer, products, login, set_cart
):
    carrot, _ = products
    login(customer)
    set_cart(
        {str(carrot.id): 11}
    )  # 壊れたセッション。get_cart が捨てるので、カートは空として扱われる
    response = client.post("/orders/", follow_redirects=True)
    assert "カートに商品がありません" in response.text
    assert all_orders() == []


# --- 決済（Stripe。ここではフェイク）との連携 ---


def test_決済ページを作れないと注文はキャンセルされカートに戻る(
    client, customer, products, login, set_cart, read_cart, fake_payment
):
    carrot, _ = products
    message = "決済ページを作れませんでした。時間をおいてもう一度お試しください"
    fake_payment.error = PaymentError(message)
    login(customer)
    set_cart({str(carrot.id): 3})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    (order,) = all_orders()
    assert order.status == OrderStatus.CANCELLED
    assert order.stripe_checkout_session_id is None
    assert read_cart() == {str(carrot.id): 3}  # カートは残る
    assert message in client.get("/cart/").text
    assert available_stock(carrot) == 10  # 押さえていた在庫が戻る


def test_在庫不足のときは決済ページを作らない(
    client, customer, products, login, set_cart, fake_payment
):
    _, pork = products  # 在庫 5
    login(customer)
    set_cart({str(pork.id): 6})
    client.post("/orders/")
    assert fake_payment.created == []
    assert all_orders() == []


def test_空のカートでは決済ページを作らない(client, customer, login, fake_payment):
    login(customer)
    client.post("/orders/")
    assert fake_payment.created == []


def test_未ログインでは決済ページを作らない(client, products, set_cart, fake_payment):
    carrot, _ = products
    set_cart({str(carrot.id): 1})
    client.post("/orders/")
    assert fake_payment.created == []


def test_決済ページに渡る注文の金額はDBの価格(
    client, customer, products, login, set_cart, fake_payment
):
    carrot, _ = products
    login(customer)
    set_cart({str(carrot.id): 2})
    client.post("/orders/", data={"total_amount": "1", "price": "1"})
    (order,) = all_orders()
    assert fake_payment.created == [order.id]
    assert order.total_amount == 400  # フェイクに渡った注文も DB の価格で計算されている


# --- 中断ページ（GET・POST /orders/<id>/cancel） ---


@pytest.fixture
def pending_order(customer, products, make_order):
    """決済ページを作ったあとの、支払い待ちの注文"""
    carrot, _ = products
    order = make_order(customer, [(carrot, 3)])
    order.stripe_checkout_session_id = f"cs_test_fake_{order.id}"
    db.session.commit()
    return order


def test_中断ページを見られる_状態は変わらない(
    client, customer, pending_order, login, fake_payment
):
    login(customer)
    response = client.get(f"/orders/{pending_order.id}/cancel")
    assert response.status_code == 200
    assert "この注文を取り消す" in response.text
    assert f'action="/orders/{pending_order.id}/cancel"' in response.text
    # 見ただけでは取り消されない
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING
    assert fake_payment.expired == []


@pytest.mark.parametrize("status", [OrderStatus.PAID, OrderStatus.CANCELLED])
def test_支払い待ち以外の中断ページには取り消しのボタンが出ない(
    client, customer, products, make_order, login, status
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 1)], status=status)
    login(customer)
    text = client.get(f"/orders/{order.id}/cancel").text
    assert "この注文を取り消す" not in text
    assert "取り消せません" in text


def test_注文を取り消すとSessionを期限切れにしてからキャンセルされる(
    client, customer, products, pending_order, login, fake_payment
):
    carrot, _ = products
    assert available_stock(carrot) == 7  # 支払い待ちが3個押さえている
    login(customer)
    response = client.post(f"/orders/{pending_order.id}/cancel")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/orders/{pending_order.id}"
    assert fake_payment.expired == [f"cs_test_fake_{pending_order.id}"]
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.CANCELLED
    assert available_stock(carrot) == 10  # 在庫の押さえが戻る
    assert "注文をキャンセルしました" in client.get(response.headers["Location"]).text


def test_Sessionを期限切れにできなければ取り消さず注文詳細へ移る(
    client, customer, pending_order, login, fake_payment
):
    message = "決済ページを閉じられなかったため、注文はキャンセルしていません。"
    fake_payment.error = PaymentError(message)
    login(customer)
    response = client.post(f"/orders/{pending_order.id}/cancel")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/orders/{pending_order.id}"
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING  # 取り消されない
    assert message in client.get(response.headers["Location"]).text


def test_期限切れにしている間に支払い済みになったら取り消さない(
    client, customer, pending_order, login, fake_payment
):
    # expire のあと cancel までの間に、Webhook が注文を paid にした場合を再現する
    def paid_while_expiring(session_id):
        db.session.execute(
            db.update(Order)
            .where(Order.id == pending_order.id)
            .values(status=OrderStatus.PAID)
        )
        db.session.commit()

    fake_payment.expire_session = paid_while_expiring
    login(customer)
    response = client.post(f"/orders/{pending_order.id}/cancel")
    assert response.status_code == 302
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PAID  # キャンセルで上書きしない
    assert (
        "この注文はキャンセルできません"
        in client.get(response.headers["Location"]).text
    )


@pytest.mark.parametrize("status", [OrderStatus.PAID, OrderStatus.CANCELLED])
def test_支払い待ち以外の注文はStripeを呼ばずキャンセルできない(
    client, customer, pending_order, login, fake_payment, status
):
    pending_order.status = status
    db.session.commit()
    login(customer)
    response = client.post(f"/orders/{pending_order.id}/cancel")
    assert response.status_code == 302
    assert fake_payment.expired == []
    db.session.refresh(pending_order)
    assert pending_order.status == status
    assert (
        "この注文はキャンセルできません"
        in client.get(response.headers["Location"]).text
    )


def test_Sessionの無い支払い待ちの注文は期限切れにせず取り消す(
    client, customer, products, make_order, login, fake_payment
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 1)])  # stripe_checkout_session_id が無い
    login(customer)
    client.post(f"/orders/{order.id}/cancel")
    assert fake_payment.expired == []
    db.session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


def test_他の人の注文は取り消せない(
    client, other_customer, pending_order, login, fake_payment
):
    login(other_customer)
    assert client.get(f"/orders/{pending_order.id}/cancel").status_code == 403
    assert client.post(f"/orders/{pending_order.id}/cancel").status_code == 403
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING
    assert fake_payment.expired == []


def test_商品を出品した生産者でも他人の注文は取り消せない(
    client, producer, pending_order, login, fake_payment
):
    login(producer)
    assert client.post(f"/orders/{pending_order.id}/cancel").status_code == 403
    assert fake_payment.expired == []


def test_管理者は他人の注文を取り消せる(
    client, admin, pending_order, login, fake_payment
):
    login(admin)
    assert client.get(f"/orders/{pending_order.id}/cancel").status_code == 200
    response = client.post(f"/orders/{pending_order.id}/cancel")
    assert response.status_code == 302
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.CANCELLED


def test_未ログインでは中断ページも取り消しも使えない(
    client, pending_order, fake_payment
):
    for method in (client.get, client.post):
        response = method(f"/orders/{pending_order.id}/cancel")
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login")
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING
    assert fake_payment.expired == []


def test_存在しない注文の中断ページは404(client, customer, login):
    login(customer)
    assert client.get("/orders/9999/cancel").status_code == 404
    assert client.post("/orders/9999/cancel").status_code == 404


def test_CSRFトークンの無い取り消しは400(app, pending_order, fake_payment):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    assert client.post(f"/orders/{pending_order.id}/cancel").status_code == 400
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING
    assert fake_payment.expired == []


# --- 支払い完了ページ（仮。GET /orders/<id>/complete） ---


def test_支払い完了ページは確認中と出し状態を変えない(
    client, customer, pending_order, login, fake_payment
):
    login(customer)
    response = client.get(
        f"/orders/{pending_order.id}/complete?session_id=cs_test_fake_{pending_order.id}"
    )
    assert response.status_code == 200
    assert "お支払いを確認しています" in response.text
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING  # 確定は次の章
    assert fake_payment.expired == []


def test_他の人の支払い完了ページは開けない(
    client, other_customer, pending_order, login
):
    login(other_customer)
    assert client.get(f"/orders/{pending_order.id}/complete").status_code == 403


def test_管理者は支払い完了ページを開ける(client, admin, pending_order, login):
    login(admin)
    assert client.get(f"/orders/{pending_order.id}/complete").status_code == 200


def test_未ログインでは支払い完了ページを開けない(client, pending_order):
    response = client.get(f"/orders/{pending_order.id}/complete")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_存在しない注文の支払い完了ページは404(client, customer, login):
    login(customer)
    assert client.get("/orders/9999/complete").status_code == 404


# --- 決済（Stripe。ここではフェイク）との連携: 追加 ---


def test_注文ごとに別のSessionが保存され決済ページのURLも違う(
    client, customer, products, login, set_cart, fake_payment
):
    carrot, _ = products
    login(customer)
    locations = []
    for _ in range(2):
        set_cart({str(carrot.id): 1})
        response = client.post("/orders/")
        assert response.status_code == 303
        locations.append(response.headers["Location"])
    first, second = all_orders()
    assert first.id != second.id
    assert first.stripe_checkout_session_id == f"cs_test_fake_{first.id}"
    assert second.stripe_checkout_session_id == f"cs_test_fake_{second.id}"
    assert first.stripe_checkout_session_id != second.stripe_checkout_session_id
    assert locations[0] != locations[1]
    assert fake_payment.created == [first.id, second.id]


def test_決済ページを作れなかったあと同じカートで注文し直せる(
    client, customer, products, login, set_cart, read_cart, fake_payment
):
    carrot, pork = products
    fake_payment.error = PaymentError("決済ページを作れませんでした")
    login(customer)
    set_cart({str(carrot.id): 3, str(pork.id): 2})
    client.post("/orders/")
    # カートは数量まで同じまま。Stripe（フェイク）には何も作られず、Session も無い
    assert read_cart() == {str(carrot.id): 3, str(pork.id): 2}
    (failed,) = all_orders()
    assert failed.stripe_checkout_session_id is None
    assert fake_payment.created == []
    assert fake_payment.expired == []  # Session が無いので期限切れにもしない
    assert available_stock(carrot) == 10
    assert available_stock(pork) == 5

    # エラーが直ったら、同じカートのまま注文できる
    fake_payment.error = None
    response = client.post("/orders/")
    assert response.status_code == 303
    failed_after, ordered = all_orders()
    assert failed_after.status == OrderStatus.CANCELLED
    assert ordered.status == OrderStatus.PENDING
    assert ordered.stripe_checkout_session_id == f"cs_test_fake_{ordered.id}"
    assert read_cart() is None
    assert available_stock(carrot) == 7  # 押さえているのは成功した注文の分だけ
    assert available_stock(pork) == 3


def test_決済ページに渡った注文の明細はDBの注文明細と一致する(
    client, customer, products, login, set_cart, fake_payment
):
    carrot, pork = products
    login(customer)
    set_cart({str(carrot.id): 3, str(pork.id): 2})
    client.post("/orders/", data={"unit_price": "1", "quantity": "99"})
    (order,) = all_orders()
    stored = [(i.product_name, i.unit_price, i.quantity) for i in order.items]
    assert stored == [("にんじん", 200, 3), ("ポーク", 1200, 2)]
    assert fake_payment.created_items == {order.id: stored}


def test_完了ページと中断ページを見てもStripeを呼ばない(
    client, customer, pending_order, login, fake_payment
):
    # どのメソッドを呼んでも例外になるようにして、画面が開けることで「呼んでいない」ことを確かめる
    fake_payment.error = AssertionError("Stripe を呼んではいけない")
    login(customer)
    for path in ("complete", "cancel"):
        response = client.get(f"/orders/{pending_order.id}/{path}")
        assert response.status_code == 200
    assert fake_payment.created == []
    assert fake_payment.expired == []
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING


# --- 中断ページ: 追加 ---


def test_取り消しを2回続けても2回目はStripeを呼ばず取り消したまま(
    client, customer, pending_order, login, fake_payment
):
    login(customer)
    client.post(f"/orders/{pending_order.id}/cancel")
    assert fake_payment.expired == [f"cs_test_fake_{pending_order.id}"]
    response = client.post(f"/orders/{pending_order.id}/cancel")
    assert response.status_code == 302
    assert len(fake_payment.expired) == 1  # 増えない
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.CANCELLED
    assert (
        "この注文はキャンセルできません"
        in client.get(response.headers["Location"]).text
    )


def test_取り消しても他の人の同じ商品の支払い待ちの注文は変わらない(
    client, customer, other_customer, products, make_order, login, fake_payment
):
    carrot, _ = products
    mine = make_order(customer, [(carrot, 3)])
    mine.stripe_checkout_session_id = "cs_test_mine"
    theirs = make_order(other_customer, [(carrot, 2)])
    theirs.stripe_checkout_session_id = "cs_test_theirs"
    db.session.commit()
    assert available_stock(carrot) == 5
    login(customer)
    client.post(f"/orders/{mine.id}/cancel")
    db.session.refresh(mine)
    db.session.refresh(theirs)
    assert mine.status == OrderStatus.CANCELLED
    assert theirs.status == OrderStatus.PENDING
    assert fake_payment.expired == ["cs_test_mine"]  # 自分の Session だけ
    assert available_stock(carrot) == 8  # 他の人の分（2個）は押さえたまま


def test_取り消したあと同じカートで注文し直せる(
    client, customer, products, login, set_cart, fake_payment
):
    _, pork = products  # 在庫 5
    login(customer)
    set_cart({str(pork.id): 5})
    assert client.post("/orders/").status_code == 303
    (first,) = all_orders()
    # 在庫を全部押さえているので、取り消すまでは同じカートで注文できない
    set_cart({str(pork.id): 5})
    assert client.post("/orders/").status_code == 302
    assert len(all_orders()) == 1

    client.post(f"/orders/{first.id}/cancel")
    set_cart({str(pork.id): 5})
    assert client.post("/orders/").status_code == 303
    cancelled, second = all_orders()
    assert cancelled.status == OrderStatus.CANCELLED
    assert second.status == OrderStatus.PENDING
    assert fake_payment.created == [first.id, second.id]


def test_管理者が取り消しても注文者の次の注文に影響しない(
    app, client, customer, admin, products, login, set_cart, fake_payment
):
    carrot, _ = products
    login(customer)
    set_cart({str(carrot.id): 4})
    client.post("/orders/")
    (first,) = all_orders()

    admin_client = app.test_client()
    admin_client.post("/login", data={"email": admin.email, "password": "password"})
    admin_client.post(f"/orders/{first.id}/cancel")
    db.session.refresh(first)
    assert first.status == OrderStatus.CANCELLED
    assert fake_payment.expired == [f"cs_test_fake_{first.id}"]

    # テストでは app_context が出しっぱなしで g が使い回されるため、
    # Flask-Login が覚えたログイン中のユーザー（管理者）を忘れさせる
    g.pop("_login_user", None)
    set_cart({str(carrot.id): 4})
    response = client.post("/orders/")
    assert response.status_code == 303
    _, second = all_orders()
    assert second.user_id == customer.id  # 管理者ではなく注文者の注文
    assert second.status == OrderStatus.PENDING
    assert second.stripe_checkout_session_id == f"cs_test_fake_{second.id}"
    assert available_stock(carrot) == 6


# --- Session を作ったあと、注文に ID を保存できなかったとき ---


def break_saving_session_id(customer, carrot, make_order, fake_payment):
    """同じ session_id がすでに別の注文にあるようにして、保存（unique 制約）を失敗させる。

    本物の Stripe の Session の ID は重複しないので、保存の失敗を再現するための細工。
    """
    existing = make_order(customer, [(carrot, 1)], status=OrderStatus.PAID)
    existing.stripe_checkout_session_id = "cs_dup"
    db.session.commit()
    fake_payment.create_checkout_session = lambda order: (
        "cs_dup",
        "https://checkout.stripe.test/pay/cs_dup",
    )
    return existing


def only_new_order(existing):
    return db.session.scalars(db.select(Order).where(Order.id != existing.id)).one()


def test_Sessionを保存できなかったらSessionを期限切れにして注文をキャンセルする(
    client,
    customer,
    products,
    make_order,
    login,
    set_cart,
    read_cart,
    fake_payment,
    caplog,
):
    carrot, _ = products
    existing = break_saving_session_id(customer, carrot, make_order, fake_payment)
    login(customer)
    set_cart({str(carrot.id): 2})
    response = client.post("/orders/")
    # 500 にならず、カートに戻る
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert "注文を完了できませんでした" in client.get("/cart/").text
    assert fake_payment.expired == ["cs_dup"]  # 払える Session を残さない
    order = only_new_order(existing)
    assert order.status == OrderStatus.CANCELLED
    assert order.stripe_checkout_session_id is None
    assert read_cart() == {str(carrot.id): 2}  # カートは残る
    assert available_stock(carrot) == 10  # 在庫を押さえ続けない
    assert "Session の ID を注文に保存できませんでした" in caplog.text


def test_保存できなかったSessionを期限切れにできなくても注文はキャンセルされる(
    client, customer, products, make_order, login, set_cart, fake_payment
):
    carrot, _ = products
    existing = break_saving_session_id(customer, carrot, make_order, fake_payment)

    def expire_fails(session_id):
        raise PaymentError("決済ページを閉じられませんでした")

    fake_payment.expire_session = expire_fails
    login(customer)
    set_cart({str(carrot.id): 2})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert (
        only_new_order(existing).status == OrderStatus.CANCELLED
    )  # 続けてキャンセルする


def test_Sessionを保存できなかったときカートは空にならず注文し直せる(
    client, customer, products, make_order, login, set_cart, read_cart, fake_payment
):
    carrot, _ = products
    existing = break_saving_session_id(customer, carrot, make_order, fake_payment)
    login(customer)
    set_cart({str(carrot.id): 2})
    client.post("/orders/")
    del (
        fake_payment.create_checkout_session
    )  # インスタンスの差し替えを外して、元のフェイクに戻す
    response = client.post("/orders/")
    assert response.status_code == 303
    assert read_cart() is None
    failed, succeeded = db.session.scalars(
        db.select(Order).where(Order.id != existing.id).order_by(Order.id)
    ).all()
    assert failed.status == OrderStatus.CANCELLED  # 1回目（保存に失敗）
    assert succeeded.status == OrderStatus.PENDING  # 2回目（成功）
    assert succeeded.stripe_checkout_session_id == f"cs_test_fake_{succeeded.id}"
