from pathlib import Path

import pytest

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product
from marche.uploads import save_image

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def form_data(**overrides):
    data = {
        "name": "ほうれん草 1束",
        "price": "200",
        "stock": "10",
        "description": "朝採りです。",
        "is_published": "y",
    }
    data.update(overrides)
    return data


def all_products():
    return db.session.scalars(db.select(Product)).all()


# --- 正常系 ---


def test_一覧には自分の商品だけが出る(
    client, producer, other_producer, make_product, login
):
    make_product(producer, name="自分のにんじん")
    make_product(other_producer, name="他人のだいこん")
    login(producer)
    response = client.get("/producer/products")
    assert response.status_code == 200
    assert "自分のにんじん" in response.text
    assert "他人のだいこん" not in response.text


def test_商品がなければ案内が出る(client, producer, login):
    login(producer)
    assert "まだ商品がありません" in client.get("/producer/products").text


def test_一覧に価格が円で出る(client, producer, make_product, login):
    make_product(producer, price=1200)
    login(producer)
    assert "1,200円" in client.get("/producer/products").text


def test_商品を登録できる(client, producer, login):
    login(producer)
    response = client.post("/producer/products/new", data=form_data())
    assert response.status_code == 302
    assert response.headers["Location"] == "/producer/products"
    (product,) = all_products()
    assert product.producer_id == producer.id
    assert product.name == "ほうれん草 1束"
    assert product.price == 200
    assert product.stock == 10
    assert product.is_published is True
    assert product.image_filename is None


def test_公開のチェックなしで登録すると非公開になる(client, producer, login):
    login(producer)
    data = form_data()
    del data["is_published"]
    client.post("/producer/products/new", data=data)
    (product,) = all_products()
    assert product.is_published is False


def test_出品者はフォームから指定できない(client, producer, other_producer, login):
    login(producer)
    client.post(
        "/producer/products/new", data=form_data(producer_id=str(other_producer.id))
    )
    (product,) = all_products()
    assert product.producer_id == producer.id


@pytest.mark.parametrize(("price", "stock"), [("50", "0"), ("100000", "999")])
def test_価格と在庫数の境界値は登録できる(client, producer, login, price, stock):
    login(producer)
    response = client.post(
        "/producer/products/new", data=form_data(price=price, stock=stock)
    )
    assert response.status_code == 302
    (product,) = all_products()
    assert product.price == int(price)
    assert product.stock == int(stock)


def test_編集画面に今の値が入っている(client, producer, make_product, login):
    product = make_product(producer, name="編集前の名前")
    login(producer)
    response = client.get(f"/producer/products/{product.id}/edit")
    assert response.status_code == 200
    assert "編集前の名前" in response.text


def test_商品を編集できる(client, producer, make_product, login):
    product = make_product(producer, name="編集前", price=200, stock=10)
    before = product.updated_at
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=form_data(name="編集後", price="300", stock="5"),
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.name == "編集後"
    assert product.price == 300
    assert product.stock == 5
    assert product.updated_at > before


def test_公開と非公開を切り替えられる(client, producer, make_product, login):
    product = make_product(producer, is_published=False)
    login(producer)
    client.post(f"/producer/products/{product.id}/toggle")
    db.session.refresh(product)
    assert product.is_published is True
    client.post(f"/producer/products/{product.id}/toggle")
    db.session.refresh(product)
    assert product.is_published is False


def test_商品を削除できる(client, producer, make_product, login):
    product = make_product(producer)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 302
    assert all_products() == []


@pytest.mark.parametrize(
    "status", [OrderStatus.PENDING, OrderStatus.PAID, OrderStatus.CANCELLED]
)
def test_注文がある商品は削除できない(
    client, producer, customer, make_product, make_order, login, status
):
    # 支払い待ち・支払い済み・キャンセルのどれでも、明細があれば削除させない
    product = make_product(producer, name="注文された商品")
    make_order(customer, [(product, 1)], status=status)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/delete", follow_redirects=True
    )
    assert response.status_code == 200
    assert "注文がある商品は削除できません。非公開にしてください" in response.text
    assert "注文された商品" in response.text  # 一覧に残っている
    assert len(all_products()) == 1
    assert len(db.session.scalars(db.select(OrderItem)).all()) == 1
    assert len(db.session.scalars(db.select(Order)).all()) == 1


def test_注文がある商品を削除しようとしても写真は消えない(
    app, client, producer, customer, make_product, make_order, login
):
    product = make_product(producer)
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    make_order(customer, [(product, 1)])
    login(producer)
    client.post(f"/producer/products/{product.id}/delete")
    assert len(list(Path(app.config["UPLOAD_FOLDER"]).glob("*"))) == 1


def test_注文のない商品は同じ生産者の別の商品に注文があっても削除できる(
    client, producer, customer, make_product, make_order, login
):
    ordered = make_product(producer, name="注文された商品")
    free = make_product(producer, name="注文のない商品")
    make_order(customer, [(ordered, 1)])
    login(producer)
    response = client.post(f"/producer/products/{free.id}/delete")
    assert response.status_code == 302
    assert [p.name for p in all_products()] == ["注文された商品"]


def test_注文があっても非公開にはできる(
    client, producer, customer, make_product, make_order, login
):
    product = make_product(producer, is_published=True)
    make_order(customer, [(product, 1)])
    login(producer)
    client.post(f"/producer/products/{product.id}/toggle")
    db.session.refresh(product)
    assert product.is_published is False


def test_他の生産者の注文がある商品の削除は403のまま(
    client, producer, other_producer, customer, make_product, make_order, login
):
    # 所有者の確認が先。他人の商品に注文があることは教えない
    product = make_product(other_producer)
    make_order(customer, [(product, 1)])
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 403
    assert "注文がある" not in response.text


# --- 権限なし ---

PRODUCT_URLS = [
    ("get", "/producer/products"),
    ("get", "/producer/products/new"),
    ("post", "/producer/products/new"),
    ("get", "/producer/products/{id}/edit"),
    ("post", "/producer/products/{id}/edit"),
    ("post", "/producer/products/{id}/toggle"),
    ("post", "/producer/products/{id}/delete"),
]


@pytest.mark.parametrize(("method", "url"), PRODUCT_URLS)
def test_購入者は商品管理の全ての画面で403(
    client, customer, producer, make_product, login, method, url
):
    product = make_product(producer)
    login(customer)
    response = getattr(client, method)(url.format(id=product.id), data=form_data())
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "url"), PRODUCT_URLS)
def test_管理者は商品管理の全ての画面で403(
    client, admin, producer, make_product, login, method, url
):
    product = make_product(producer)
    login(admin)
    response = getattr(client, method)(url.format(id=product.id), data=form_data())
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "url"), PRODUCT_URLS)
def test_未ログインはログイン画面へ移る(client, producer, make_product, method, url):
    product = make_product(producer)
    response = getattr(client, method)(url.format(id=product.id), data=form_data())
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
    assert len(all_products()) == 1


def test_他の生産者の商品は編集できない(
    client, producer, other_producer, make_product, login
):
    product = make_product(other_producer, name="他人の商品")
    login(producer)
    url = f"/producer/products/{product.id}/edit"
    assert client.get(url).status_code == 403
    assert client.post(url, data=form_data(name="乗っ取り")).status_code == 403
    db.session.refresh(product)
    assert product.name == "他人の商品"


def test_他の生産者の商品は公開を切り替えられない(
    client, producer, other_producer, make_product, login
):
    product = make_product(other_producer, is_published=False)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/toggle")
    assert response.status_code == 403
    db.session.refresh(product)
    assert product.is_published is False


def test_他の生産者の商品は削除できない(
    client, producer, other_producer, make_product, login
):
    product = make_product(other_producer)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 403
    assert len(all_products()) == 1


@pytest.mark.parametrize("action", ["edit", "toggle", "delete"])
def test_存在しない商品は404(client, producer, login, action):
    login(producer)
    url = f"/producer/products/9999/{action}"
    response = client.get(url) if action == "edit" else client.post(url)
    assert response.status_code == 404


def test_一覧の削除と切り替えはGETでは動かない(client, producer, make_product, login):
    product = make_product(producer)
    login(producer)
    assert client.get(f"/producer/products/{product.id}/delete").status_code == 405
    assert client.get(f"/producer/products/{product.id}/toggle").status_code == 405
    assert len(all_products()) == 1


# --- 不正な入力 ---


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "あ" * 51},
        {"price": ""},
        {"price": "0"},
        {"price": "49"},
        {"price": "100001"},
        {"price": "abc"},
        {"price": "12.5"},
        {"stock": ""},
        {"stock": "-1"},
        {"stock": "1000"},
        {"stock": "abc"},
        {"description": "あ" * 2001},
    ],
)
def test_不正な入力では登録できない(client, producer, login, overrides):
    login(producer)
    response = client.post("/producer/products/new", data=form_data(**overrides))
    assert response.status_code == 422
    assert all_products() == []


def test_不正な入力では編集できない(client, producer, make_product, login):
    product = make_product(producer, name="編集前", price=200)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=form_data(name="編集後", price="49"),
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.name == "編集前"
    assert product.price == 200


def test_入力エラーは日本語で出て入力は残る(client, producer, login):
    login(producer)
    response = client.post(
        "/producer/products/new", data=form_data(name="入力したまま", price="abc")
    )
    assert response.status_code == 422
    assert "整数で入力してください" in response.text
    assert "入力したまま" in response.text


def test_在庫数0と空の紹介文は登録できる(client, producer, login):
    login(producer)
    response = client.post(
        "/producer/products/new", data=form_data(stock="0", description="")
    )
    assert response.status_code == 302
    (product,) = all_products()
    assert product.stock == 0
    assert product.description == ""


def test_商品名のHTMLはエスケープされる(client, producer, make_product, login):
    make_product(producer, name="<script>alert(1)</script>")
    login(producer)
    response = client.get("/producer/products")
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
