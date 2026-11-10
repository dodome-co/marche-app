import re
from urllib.parse import unquote

import pytest
from flask import session

from marche.cart import get_cart
from marche.extensions import db
from marche.models import Order


def add(client, product, quantity, **extra):
    return client.post(
        f"/cart/add/{product.id}", data={"quantity": str(quantity), **extra}
    )


def update(client, product, quantity):
    return client.post(f"/cart/update/{product.id}", data={"quantity": str(quantity)})


def option_values(html):
    return re.findall(r'<option value="(\d+)"', html)


# --- get_cart（セッションの読み取り） ---


def test_get_cartは文字列のキーをintにする(app):
    with app.test_request_context():
        session["cart"] = {"3": 2, "10": 1}
        assert get_cart() == {3: 2, 10: 1}


def test_カートが無ければ空(app):
    with app.test_request_context():
        assert get_cart() == {}


@pytest.mark.parametrize(
    "raw",
    [
        "x",
        [1],
        None,
        {"abc": 1},
        {"3": "2"},
        {"3": 0},
        {"3": -1},
        {"3": True},
        {"3": 1.5},
        {"3": 11},  # 1回に買える数（10）を超えている
        {"3": 999},
    ],
)
def test_壊れたカートは空として扱う(app, raw):
    with app.test_request_context():
        session["cart"] = raw
        assert get_cart() == {}


def test_数量が上限ちょうどなら有効(app):
    with app.test_request_context():
        session["cart"] = {"3": 10}
        assert get_cart() == {3: 10}


def test_同じ商品が2回入っているときは先のものだけ残す(app):
    # "3" と "03" はどちらも商品ID 3。後ろのもので上書きしない
    with app.test_request_context():
        session["cart"] = {"3": 1, "03": 5}
        assert get_cart() == {3: 1}


def test_壊れた項目だけを無視する(app):
    with app.test_request_context():
        session["cart"] = {"3": 2, "x": 1, "4": 0}
        assert get_cart() == {3: 2}


# --- カートに入れる ---


def test_商品をカートに入れるとカートの画面に移る(
    client, producer, make_product, read_cart
):
    product = make_product(producer, stock=10, is_published=True)
    response = add(client, product, 2)
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert read_cart() == {str(product.id): 2}


def test_同じ商品は数量が足される(client, producer, make_product, read_cart):
    product = make_product(producer, stock=10, is_published=True)
    add(client, product, 2)
    add(client, product, 3)
    assert read_cart() == {str(product.id): 5}


def test_合計がちょうど10までは入り_超えると入らない(
    client, producer, make_product, read_cart
):
    product = make_product(producer, name="にんじん", stock=50, is_published=True)
    add(client, product, 5)
    add(client, product, 5)
    assert read_cart() == {str(product.id): 10}
    response = add(client, product, 1)
    assert response.status_code == 302
    assert response.headers["Location"] == f"/products/{product.id}"
    assert read_cart() == {str(product.id): 10}
    text = client.get(f"/products/{product.id}").text
    assert "「にんじん」はカートに10個入っています。合計で10個まで買えます" in text


def test_買える数を超える数量は入らない(client, producer, make_product, read_cart):
    product = make_product(producer, name="にんじん", stock=3, is_published=True)
    add(client, product, 4)
    assert read_cart() is None
    assert "「にんじん」は3個まで買えます" in client.get(f"/products/{product.id}").text
    add(client, product, 3)
    assert read_cart() == {str(product.id): 3}


def test_支払い待ちの注文が押さえた分は上限から引かれる(
    client, producer, customer, make_product, make_order, read_cart
):
    product = make_product(producer, name="にんじん", stock=5, is_published=True)
    make_order(customer, [(product, 3)])
    add(client, product, 3)
    assert read_cart() is None
    add(client, product, 2)
    assert read_cart() == {str(product.id): 2}


def test_売り切れの商品は入らない(client, producer, make_product, read_cart):
    product = make_product(producer, name="ピクルス", stock=0, is_published=True)
    add(client, product, 1)
    assert read_cart() is None
    assert "「ピクルス」は売り切れです" in client.get(f"/products/{product.id}").text


def test_非公開の商品はカートに入れられない(client, producer, make_product, read_cart):
    product = make_product(producer, stock=10, is_published=False)
    assert add(client, product, 1).status_code == 404
    assert read_cart() is None


def test_出品者本人でも非公開の商品はカートに入れられない(
    client, producer, make_product, login, read_cart
):
    product = make_product(producer, stock=10, is_published=False)
    login(producer)
    assert add(client, product, 1).status_code == 404
    assert read_cart() is None


def test_存在しない商品はカートに入れられない(client):
    assert client.post("/cart/add/9999", data={"quantity": "1"}).status_code == 404


@pytest.mark.parametrize("quantity", ["0", "-1", "abc", "", "1.5", " "])
def test_数量が不正ならカートは変わらない(
    client, producer, make_product, read_cart, quantity
):
    product = make_product(producer, stock=10, is_published=True)
    response = client.post(f"/cart/add/{product.id}", data={"quantity": quantity})
    assert response.status_code == 302
    assert response.headers["Location"] == f"/products/{product.id}"
    assert read_cart() is None


def test_数量が送られなくてもカートは変わらない(
    client, producer, make_product, read_cart
):
    product = make_product(producer, stock=10, is_published=True)
    response = client.post(f"/cart/add/{product.id}", data={})
    assert response.status_code == 302
    assert read_cart() is None


def test_金額を送っても無視される(client, producer, make_product, read_cart):
    product = make_product(producer, price=200, stock=10, is_published=True)
    add(client, product, 2, price="1", total_amount="1", unit_price="1")
    assert read_cart() == {str(product.id): 2}  # 数量だけが入る
    text = client.get("/cart/").text
    assert "200円" in text
    assert "400円" in text


def test_カートに入れるのにログインは要らない(client, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    assert add(client, product, 1).status_code == 302


# --- カートの画面 ---


def test_カートの中身と合計が出る(client, producer, make_product):
    carrot = make_product(
        producer, name="にんじん", price=200, stock=10, is_published=True
    )
    pork = make_product(producer, name="ポーク", price=1200, stock=5, is_published=True)
    add(client, carrot, 3)
    add(client, pork, 2)
    text = client.get("/cart/").text
    for expected in (
        "にんじん",
        "200円",
        "600円",
        "ポーク",
        "1,200円",
        "2,400円",
        "3,000円",
    ):
        assert expected in text


def test_単価はセッションではなくDBから取る(client, producer, make_product):
    product = make_product(producer, price=200, stock=10, is_published=True)
    add(client, product, 2)
    product.price = 500
    db.session.commit()
    text = client.get("/cart/").text
    assert "500円" in text
    assert "1,000円" in text
    assert "200円" not in text


def test_空のカートには案内が出て注文のボタンは出ない(client):
    text = client.get("/cart/").text
    assert "カートは空です" in text
    assert "注文して支払いへ進む" not in text
    assert "ログインして注文する" not in text


def test_カートは未ログインでも見られる(client):
    assert client.get("/cart/").status_code == 200


def test_非公開になった商品は表示されずカートから外れる(
    client, producer, make_product, read_cart
):
    kept = make_product(producer, name="残る商品", stock=10, is_published=True)
    hidden = make_product(producer, name="隠れる商品", stock=10, is_published=True)
    add(client, kept, 1)
    add(client, hidden, 1)
    hidden.is_published = False
    db.session.commit()
    text = client.get("/cart/").text
    assert "残る商品" in text
    assert "隠れる商品" not in text
    assert "購入できなくなった商品をカートから外しました" in text
    assert read_cart() == {str(kept.id): 1}
    # 知らせるのは1回だけ
    assert (
        "購入できなくなった商品をカートから外しました" not in client.get("/cart/").text
    )


def test_壊れた値は知らせずにセッションから消える(
    client, producer, make_product, set_cart, read_cart
):
    kept = make_product(producer, name="残る商品", stock=10, is_published=True)
    # 数字でないキー、上限を超える数量、重複したキー。
    # セッションは Cookie に保存されるときキーが並べ替えられるので、
    # 重複のキーは並べ替えたあとも後ろに来る形（末尾に空白。int("1 ") は 1）にする
    set_cart({str(kept.id): 2, "abc": 1, "7": 99, f"{kept.id} ": 5})
    text = client.get("/cart/").text
    assert "残る商品" in text
    assert "購入できなくなった商品をカートから外しました" not in text
    assert read_cart() == {str(kept.id): 2}


def test_形が壊れたカートは空になる(client, set_cart, read_cart):
    set_cart("x")
    response = client.get("/cart/")
    assert response.status_code == 200
    assert "カートは空です" in response.text
    assert read_cart() is None


def test_売り切れの行には売り切れと出る(client, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    add(client, product, 2)
    product.stock = 0
    db.session.commit()
    text = client.get("/cart/").text
    assert "売り切れです。数量を0にして外してください" in text
    assert "在庫が足りません" not in text


def test_注文のボタンは送信後に押せなくなる(
    client, producer, customer, make_product, login
):
    product = make_product(producer, stock=10, is_published=True)
    login(customer)
    add(client, product, 1)
    text = client.get("/cart/").text
    form_tag = re.search(r'<form[^>]*action="/orders/"[^>]*>', text).group(0)
    assert "onsubmit=" in form_tag
    assert "disabled = true" in form_tag  # 二度押しで注文が2件できないように


def test_存在しなくなった商品もカートから外れる(
    client, producer, make_product, set_cart, read_cart
):
    kept = make_product(producer, name="残る商品", stock=10, is_published=True)
    set_cart({"9999": 1, str(kept.id): 1})
    text = client.get("/cart/").text
    assert "残る商品" in text
    assert "購入できなくなった商品をカートから外しました" in text
    assert read_cart() == {str(kept.id): 1}


def test_全部外れるとカートは空になる(client, producer, make_product, read_cart):
    product = make_product(producer, stock=10, is_published=True)
    add(client, product, 1)
    product.is_published = False
    db.session.commit()
    assert "カートは空です" in client.get("/cart/").text
    assert read_cart() is None


def test_買える数を超えた数量には警告が出る(
    client, producer, customer, make_product, make_order, set_cart
):
    product = make_product(producer, stock=5, is_published=True)
    set_cart({str(product.id): 5})
    make_order(customer, [(product, 3)])  # あとから他の人が押さえた
    text = client.get("/cart/").text
    assert "在庫が足りません（いま買える数: 2）" in text


def test_カートの商品名はエスケープされる(client, producer, make_product):
    product = make_product(
        producer, name="<script>alert(1)</script>", stock=10, is_published=True
    )
    add(client, product, 1)
    text = client.get("/cart/").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


def test_未ログインは注文のボタンの代わりにログインのリンクが出る(
    client, producer, make_product, customer, read_cart
):
    product = make_product(producer, stock=10, is_published=True)
    add(client, product, 1)
    text = client.get("/cart/").text
    assert "注文して支払いへ進む" not in text
    assert "ログインして注文する" in text
    href = re.search(r'href="(/login[^"]*)"[^>]*>ログインして注文する', text).group(1)
    assert unquote(href) == "/login?next=/cart/"
    # このリンクからログインすると、カートに戻る
    response = client.post(href, data={"email": customer.email, "password": "password"})
    assert response.headers["Location"] == "/cart/"
    assert read_cart() == {str(product.id): 1}  # ログインしてもカートは残る


def test_ログイン中は注文のボタンが出る(
    client, producer, customer, make_product, login
):
    product = make_product(producer, stock=10, is_published=True)
    login(customer)
    add(client, product, 1)
    text = client.get("/cart/").text
    assert "注文して支払いへ進む" in text
    assert "ログインして注文する" not in text


def test_注文のフォームが送るのはCSRFトークンだけ(
    client, producer, customer, make_product, login
):
    product = make_product(producer, stock=10, is_published=True)
    login(customer)
    add(client, product, 2)
    text = client.get("/cart/").text
    form = re.search(r'<form[^>]*action="/orders/"[^>]*>(.*?)</form>', text, re.DOTALL)
    names = re.findall(r'<(?:input|select|textarea)[^>]*name="(\w+)"', form.group(1))
    assert names == ["csrf_token"]  # 金額も数量も送らない


def test_数量の変更フォームは数量とCSRFトークンだけ(client, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    add(client, product, 2)
    text = client.get("/cart/").text
    forms = re.findall(
        r'<form[^>]*action="/cart/update/\d+"[^>]*>(.*?)</form>', text, re.DOTALL
    )
    assert len(forms) == 1
    names = re.findall(r'<(?:input|select|textarea)[^>]*name="(\w+)"', forms[0])
    assert sorted(names) == ["csrf_token", "quantity"]


def test_ヘッダーにカートのリンクが誰にでも出る(client, customer, login):
    assert 'href="/cart/"' in client.get("/").text
    login(customer)
    assert 'href="/cart/"' in client.get("/").text


# --- 数量の変更 ---


def test_数量を変えられる(client, producer, make_product, set_cart, read_cart):
    product = make_product(producer, stock=10, is_published=True)
    set_cart({str(product.id): 2})
    response = update(client, product, 4)
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert read_cart() == {str(product.id): 4}


@pytest.mark.parametrize("quantity", [0, -1, -100])
def test_0以下にするとカートから外れる(
    client, producer, make_product, set_cart, read_cart, quantity
):
    product = make_product(producer, stock=10, is_published=True)
    other = make_product(producer, name="残る商品", stock=10, is_published=True)
    set_cart({str(product.id): 2, str(other.id): 1})
    update(client, product, quantity)
    assert read_cart() == {str(other.id): 1}


def test_最後の1つを外すとカートが空になる(
    client, producer, make_product, set_cart, read_cart
):
    product = make_product(producer, stock=10, is_published=True)
    set_cart({str(product.id): 2})
    update(client, product, 0)
    assert read_cart() is None


@pytest.mark.parametrize("quantity", [11, 100])
def test_上限を超える数量には変えられない(
    client, producer, make_product, set_cart, read_cart, quantity
):
    product = make_product(producer, name="にんじん", stock=50, is_published=True)
    set_cart({str(product.id): 2})
    update(client, product, quantity)
    assert read_cart() == {str(product.id): 2}
    assert "「にんじん」は10個まで買えます" in client.get("/cart/").text


def test_買える数を超える数量には変えられない(
    client, producer, make_product, set_cart, read_cart
):
    product = make_product(producer, name="にんじん", stock=3, is_published=True)
    set_cart({str(product.id): 1})
    update(client, product, 4)
    assert read_cart() == {str(product.id): 1}
    assert "「にんじん」は3個まで買えます" in client.get("/cart/").text


@pytest.mark.parametrize("quantity", ["abc", "", "1.5"])
def test_数量が整数でなければ変えない(
    client, producer, make_product, set_cart, read_cart, quantity
):
    product = make_product(producer, stock=10, is_published=True)
    set_cart({str(product.id): 2})
    client.post(f"/cart/update/{product.id}", data={"quantity": quantity})
    assert read_cart() == {str(product.id): 2}


def test_カートに無い商品は変えられない(client, producer, make_product, read_cart):
    product = make_product(producer, stock=10, is_published=True)
    response = update(client, product, 3)
    assert response.status_code == 302
    assert read_cart() is None
    assert "カートに入っていない商品です" in client.get("/cart/").text


def test_非公開になった商品の変更はカートから外す(
    client, producer, make_product, set_cart, read_cart
):
    product = make_product(producer, stock=10, is_published=False)
    set_cart({str(product.id): 2})
    update(client, product, 3)
    assert read_cart() is None


def test_存在しなくなった商品の変更もカートから外す(client, set_cart, read_cart):
    set_cart({"9999": 1})
    response = client.post("/cart/update/9999", data={"quantity": "2"})
    assert response.status_code == 302
    assert read_cart() is None
    assert "購入できなくなった商品をカートから外しました" in client.get("/cart/").text


# --- 商品詳細のカートに入れるフォーム ---


def test_数量の選択肢は買える数まで(client, producer, make_product):
    product = make_product(producer, stock=3, is_published=True)
    assert option_values(client.get(f"/products/{product.id}").text) == ["1", "2", "3"]


def test_数量の選択肢は10まで(client, producer, make_product):
    product = make_product(producer, stock=50, is_published=True)
    values = option_values(client.get(f"/products/{product.id}").text)
    assert values == [str(n) for n in range(1, 11)]


def test_支払い待ちの分だけ数量の選択肢が減る(
    client, producer, customer, make_product, make_order
):
    product = make_product(producer, stock=5, is_published=True)
    make_order(customer, [(product, 3)])
    assert option_values(client.get(f"/products/{product.id}").text) == ["1", "2"]


def test_売り切れにはカートに入れるボタンが出ない(client, producer, make_product):
    product = make_product(producer, stock=0, is_published=True)
    text = client.get(f"/products/{product.id}").text
    assert "売り切れ" in text
    assert "カートに入れる" not in text
    assert option_values(text) == []


def test_非公開の商品を出品者が見てもボタンは出ない(
    client, producer, make_product, login
):
    product = make_product(producer, stock=10, is_published=False)
    login(producer)
    text = client.get(f"/products/{product.id}").text
    assert "この商品は非公開です" in text
    assert "カートに入れる" not in text


def test_公開中の商品には未ログインでもボタンが出る(client, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    text = client.get(f"/products/{product.id}").text
    assert "カートに入れる" in text
    assert f'action="/cart/add/{product.id}"' in text


# --- CSRF ---


def test_CSRFトークンの無いカートのPOSTは400(app, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    assert (
        client.post(f"/cart/add/{product.id}", data={"quantity": "1"}).status_code
        == 400
    )
    assert (
        client.post(f"/cart/update/{product.id}", data={"quantity": "1"}).status_code
        == 400
    )


def test_カートの操作では注文は作られない(client, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    add(client, product, 2)
    client.get("/cart/")
    assert db.session.scalars(db.select(Order)).all() == []
