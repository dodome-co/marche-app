from datetime import datetime

import pytest

from marche.extensions import db
from marche.models import JST, Order, OrderItem, OrderStatus


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


def test_カートから注文できる(client, customer, products, login, set_cart, read_cart):
    carrot, pork = products
    login(customer)
    set_cart({str(carrot.id): 3, str(pork.id): 2})
    response = client.post("/orders/")
    (order,) = all_orders()
    assert response.status_code == 302
    assert response.headers["Location"] == f"/orders/{order.id}"
    assert order.user_id == customer.id
    assert order.status == OrderStatus.PENDING
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
    assert response.status_code == 302
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
        "お支払いの機能は準備中です",
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
def test_支払い待ち以外には準備中の案内が出ない(
    client, customer, products, make_order, login, status, label
):
    carrot, _ = products
    order = make_order(customer, [(carrot, 1)], status=status)
    login(customer)
    text = client.get(f"/orders/{order.id}").text
    assert label in text
    assert "お支払いの機能は準備中です" not in text


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

    response = client.post("/orders/", follow_redirects=True)
    assert response.status_code == 200
    assert "ご注文を受け付けました" in response.text
    assert "支払い待ち" in response.text
    assert "3,000円" in response.text
    assert "カートは空です" in client.get("/cart/").text

    (order,) = all_orders()
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
