import pytest

from marche import create_app
from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, Role, User

# 本物の外部サービス（Stripe、拡張で足す Claude API）は、テストでは呼ばない。
# create_app() の設定でフェイク（偽物）に差し替える（拡張で AI を足すときは、ここに FakeAI を足す）。


class FakePayment:
    """services/payment.py の代わりに使うフェイク。呼ばれた内容を記録し、決まった値を返す"""

    def __init__(self):
        self.created = []  # create_checkout_session に渡された注文の ID
        self.sessions = {}  # retrieve_session が返す {session_id: Session のようなもの}
        self.expired = []  # expire_session に渡された session_id
        self.error = None  # 例外を入れておくと、どのメソッドもそれを投げる
        # create_checkout_session に渡された注文の明細 {注文ID: [(商品名, 単価, 数量), ...]}
        self.created_items = {}

    def create_checkout_session(self, order):
        if self.error:
            raise self.error
        self.created.append(order.id)
        self.created_items[order.id] = [
            (item.product_name, item.unit_price, item.quantity) for item in order.items
        ]
        session_id = f"cs_test_fake_{order.id}"
        return session_id, f"https://checkout.stripe.test/pay/{session_id}"

    def retrieve_session(self, session_id):
        if self.error:
            raise self.error
        return self.sessions[session_id]

    def expire_session(self, session_id):
        if self.error:
            raise self.error
        self.expired.append(session_id)


@pytest.fixture(autouse=True)
def block_real_stripe(monkeypatch):
    """どのテストでも、本物の Stripe を呼ばないようにする。

    .env のキーが環境に残っていても、うっかり本物を呼ばない。
    services/payment.py そのもののテストは、この差し替えを上書きして自分のフェイクを使う。
    """

    def blocked():
        raise AssertionError("テストで本物の Stripe を呼ぼうとしました")

    monkeypatch.setattr("marche.services.payment._client", blocked)


@pytest.fixture
def fake_payment():
    return FakePayment()


@pytest.fixture
def app(tmp_path, fake_payment):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            # テストの写真が marche/static/uploads に溜まらないように、一時フォルダーに置く
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "PAYMENT_SERVICE": fake_payment,
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(role, email, name):
    user = User(name=name, email=email, role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def customer(app):
    return _make_user(Role.CUSTOMER, "customer@example.com", "購入者")


@pytest.fixture
def other_customer(app):
    return _make_user(Role.CUSTOMER, "other-customer@example.com", "別の購入者")


@pytest.fixture
def set_cart(client):
    """セッションのカートを直接書き込む。cart は {"商品ID": 数量}"""

    def _set_cart(cart):
        with client.session_transaction() as sess:
            sess["cart"] = cart

    return _set_cart


@pytest.fixture
def read_cart(client):
    """セッションのカートを読む。無ければ None"""

    def _read_cart():
        with client.session_transaction() as sess:
            return sess.get("cart")

    return _read_cart


@pytest.fixture
def producer(app):
    return _make_user(Role.PRODUCER, "producer@example.com", "高原ファーム")


@pytest.fixture
def other_producer(app):
    return _make_user(Role.PRODUCER, "other@example.com", "別の農園")


@pytest.fixture
def admin(app):
    return _make_user(Role.ADMIN, "admin@example.com", "管理者")


@pytest.fixture
def make_product(app):
    def _make_product(owner, name="ほうれん草 1束", **kwargs):
        values = {"price": 200, "stock": 10, "is_published": False} | kwargs
        product = Product(producer_id=owner.id, name=name, **values)
        db.session.add(product)
        db.session.commit()
        return product

    return _make_product


@pytest.fixture
def make_order(app):
    """注文を作る。items は (商品, 数量) の並び。金額は商品の今の価格から入れる"""

    def _make_order(user, items, status=OrderStatus.PENDING, created_at=None):
        order = Order(user_id=user.id, status=status, total_amount=0)
        if created_at is not None:
            order.created_at = created_at
        for product, quantity in items:
            subtotal = product.price * quantity
            order.items.append(
                OrderItem(
                    product_id=product.id,
                    product_name=product.name,
                    unit_price=product.price,
                    quantity=quantity,
                    subtotal=subtotal,
                )
            )
            order.total_amount += subtotal
        db.session.add(order)
        db.session.commit()
        return order

    return _make_order


@pytest.fixture
def login(client):
    def do_login(user):
        return client.post("/login", data={"email": user.email, "password": "password"})

    return do_login
