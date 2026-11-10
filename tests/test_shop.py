from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from marche.extensions import db
from marche.models import Order, OrderItem, Product, Role, User, now_jst
from marche.services.inventory import PENDING_MINUTES
from marche.uploads import save_image

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def minutes_ago(minutes):
    return now_jst() - timedelta(minutes=minutes)


# --- 一覧 ---


def test_公開中の商品だけが一覧に出る(client, producer, other_producer, make_product):
    make_product(producer, name="公開のにんじん", is_published=True)
    make_product(producer, name="非公開のだいこん", is_published=False)
    make_product(other_producer, name="他の農園のりんご", is_published=True)
    response = client.get("/")
    assert response.status_code == 200
    assert "公開のにんじん" in response.text
    assert "他の農園のりんご" in response.text
    assert "非公開のだいこん" not in response.text


@pytest.mark.parametrize("role", ["producer", "admin", "customer"])
def test_ログイン中でも非公開の商品は一覧に出ない(
    client, request, login, producer, make_product, role
):
    # 出品した本人や管理者は詳細は見られるが、一覧には公開中の商品だけが出る
    make_product(producer, name="非公開のだいこん", is_published=False)
    make_product(producer, name="公開のにんじん", is_published=True)
    login(request.getfixturevalue(role))
    text = client.get("/").text
    assert "公開のにんじん" in text
    assert "非公開のだいこん" not in text


def test_一覧には商品名と価格と生産者名と在庫が出る(client, producer, make_product):
    make_product(producer, name="ポーク", price=1200, stock=8, is_published=True)
    text = client.get("/").text
    assert "ポーク" in text
    assert "1,200円" in text
    assert producer.name in text
    assert "在庫 8" in text
    assert "売り切れ" not in text


def test_在庫0は売り切れと出る(client, producer, make_product):
    make_product(producer, stock=0, is_published=True)
    text = client.get("/").text
    assert "売り切れ" in text
    assert "在庫 0" not in text


def test_支払い待ちの数だけ一覧の在庫が減る(
    client, producer, customer, make_product, make_order
):
    product = make_product(producer, stock=10, is_published=True)
    make_order(customer, [(product, 3)])
    assert "在庫 7" in client.get("/").text


def test_35分を過ぎた支払い待ちは一覧の在庫を減らさない(
    client, producer, customer, make_product, make_order
):
    product = make_product(producer, stock=10, is_published=True)
    make_order(customer, [(product, 3)], created_at=minutes_ago(PENDING_MINUTES + 1))
    assert "在庫 10" in client.get("/").text


def test_支払い待ちが在庫を全部押さえると売り切れ(
    client, producer, customer, make_product, make_order
):
    product = make_product(producer, stock=3, is_published=True)
    make_order(customer, [(product, 3)])
    assert "売り切れ" in client.get("/").text


def test_一覧は新しい順に並ぶ(client, producer, make_product):
    make_product(
        producer, name="古い商品", is_published=True, created_at=minutes_ago(60)
    )
    make_product(
        producer, name="新しい商品", is_published=True, created_at=minutes_ago(1)
    )
    text = client.get("/").text
    assert text.index("新しい商品") < text.index("古い商品")


def test_写真があれば一覧に出る(client, producer, make_product):
    make_product(producer, name="写真つき", is_published=True, image_filename="abc.jpg")
    assert "/static/uploads/abc.jpg" in client.get("/").text


def test_写真が無い商品の一覧には画像を出さない(client, producer, make_product):
    make_product(producer, name="写真なし", is_published=True)
    assert "<img" not in client.get("/").text


def test_商品がなければ一覧に案内が出る(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "いま買える商品はありません" in response.text


def test_未ログインでも一覧を見られる(client, producer, make_product):
    make_product(producer, name="にんじん", is_published=True)
    assert client.get("/").status_code == 200


# --- 詳細 ---


def test_公開中の詳細は未ログインでも見られる(client, producer, make_product):
    product = make_product(
        producer,
        name="ほうれん草",
        price=180,
        stock=30,
        description="朝採りです。",
        is_published=True,
    )
    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    text = response.text
    assert "ほうれん草" in text
    assert "180円" in text
    assert "朝採りです。" in text
    assert producer.name in text
    assert "いま買える数: 30" in text
    assert "非公開" not in text


def test_詳細に写真が出る(client, producer, make_product):
    product = make_product(producer, is_published=True, image_filename="abc.jpg")
    assert "/static/uploads/abc.jpg" in client.get(f"/products/{product.id}").text


def test_写真が無い商品の詳細には画像を出さない(client, producer, make_product):
    product = make_product(producer, is_published=True)
    assert "<img" not in client.get(f"/products/{product.id}").text


def test_紹介文の改行が保たれる(client, producer, make_product):
    product = make_product(producer, description="一行目\n二行目", is_published=True)
    text = client.get(f"/products/{product.id}").text
    assert "一行目\n二行目" in text
    assert "white-space: pre-line" in text


def test_詳細も支払い待ちの数だけ減る(
    client, producer, customer, make_product, make_order
):
    product = make_product(producer, stock=10, is_published=True)
    make_order(customer, [(product, 4)])
    assert "いま買える数: 6" in client.get(f"/products/{product.id}").text


def test_在庫0の詳細は売り切れと出る(client, producer, make_product):
    product = make_product(producer, stock=0, is_published=True)
    text = client.get(f"/products/{product.id}").text
    assert "売り切れ" in text
    assert "いま買える数" not in text


def test_非公開の詳細は未ログインだと404(client, producer, make_product):
    product = make_product(producer, is_published=False)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は購入者だと404(client, producer, customer, make_product, login):
    product = make_product(producer, is_published=False)
    login(customer)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は他の生産者だと404(
    client, producer, other_producer, make_product, login
):
    product = make_product(producer, is_published=False)
    login(other_producer)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は出品した生産者なら見られる(
    client, producer, make_product, login
):
    product = make_product(producer, name="準備中の商品", is_published=False)
    login(producer)
    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    assert "準備中の商品" in response.text
    assert "この商品は非公開です" in response.text


def test_非公開の詳細は管理者なら見られる(client, producer, admin, make_product, login):
    product = make_product(producer, is_published=False)
    login(admin)
    assert client.get(f"/products/{product.id}").status_code == 200


@pytest.mark.parametrize("path", ["/products/9999", "/products/abc", "/products/-1"])
def test_存在しない商品の詳細は404(client, path):
    assert client.get(path).status_code == 404


def test_商品の情報はエスケープされて表示される(client, producer, make_product):
    producer.name = "<b>農園</b>"
    db.session.commit()
    product = make_product(
        producer,
        name="<script>alert(1)</script>",
        description="<img src=x onerror=alert(2)>",
        is_published=True,
    )
    for url in ("/", f"/products/{product.id}"):
        text = client.get(url).text
        assert "<script>alert(1)</script>" not in text
        assert "<img src=x onerror=alert(2)>" not in text
        assert "<b>農園</b>" not in text
        assert "&lt;script&gt;" in text


# --- flask seed ---


def run_seed(app, *args, **kwargs):
    return app.test_cli_runner().invoke(args=["seed", *args], **kwargs)


def test_seedで5人と8商品が入る(app):
    result = run_seed(app, "--yes")
    assert result.exit_code == 0
    users = db.session.scalars(db.select(User)).all()
    assert len(users) == 5
    roles = sorted(user.role for user in users)
    assert roles == sorted(
        [Role.ADMIN, Role.PRODUCER, Role.PRODUCER, Role.CUSTOMER, Role.CUSTOMER]
    )
    assert len(db.session.scalars(db.select(Product)).all()) == 8


def test_seedの商品は仕様どおり(app):
    run_seed(app, "--yes")
    products = {p.name: p for p in db.session.scalars(db.select(Product))}
    assert products["八幡平ポーク ロース 300g"].price == 1200
    assert products["八幡平ポーク ロース 300g"].producer.name == "岩手山麓工房"
    assert products["ほうれん草 1束"].producer.name == "高原ファーム"
    assert products["試作中の干し野菜セット"].is_published is False
    assert products["地熱野菜のピクルス"].stock == 0
    assert products["地熱野菜のピクルス"].is_published is True
    assert sum(p.is_published for p in products.values()) == 7
    assert all(p.image_filename is None for p in products.values())


def test_seedでは注文を入れない(app):
    run_seed(app, "--yes")
    assert db.session.scalars(db.select(Order)).all() == []


def test_seedで入れたユーザーでログインできる(app, client):
    run_seed(app, "--yes")
    for email in (
        "admin@example.com",
        "producer1@example.com",
        "customer2@example.com",
    ):
        response = client.post("/login", data={"email": email, "password": "password"})
        assert response.status_code == 302
        client.post("/logout")


def test_seedを2回実行しても増えない(app):
    run_seed(app, "--yes")
    run_seed(app, "--yes")
    assert len(db.session.scalars(db.select(User)).all()) == 5
    assert len(db.session.scalars(db.select(Product)).all()) == 8


def test_seedは既存のデータと写真を消す(
    app, customer, producer, make_product, make_order
):
    product = make_product(producer, name="消えるはずの商品")
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    make_order(customer, [(product, 1)])
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    assert len(list(upload_folder.glob("*"))) == 1
    # seed と同じセッションに古いオブジェクトが残らないよう、メールを控えて外しておく
    customer_email = customer.email
    db.session.expunge_all()

    run_seed(app, "--yes")

    assert db.session.scalar(db.select(User).filter_by(email=customer_email)) is None
    assert (
        db.session.scalar(db.select(Product).filter_by(name="消えるはずの商品")) is None
    )
    assert db.session.scalars(db.select(Order)).all() == []
    assert db.session.scalars(db.select(OrderItem)).all() == []
    assert list(upload_folder.glob("*")) == []


def test_seedは確認で断ると何も消さない(
    app, customer, producer, make_product, make_order
):
    product = make_product(producer, name="消えてはいけない商品")
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    make_order(customer, [(product, 1)])
    customer_email = customer.email
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    db.session.expunge_all()

    result = run_seed(app, input="n\n")

    assert result.exit_code != 0
    assert db.session.scalar(db.select(User).filter_by(email=customer_email))
    assert db.session.scalar(db.select(Product).filter_by(name="消えてはいけない商品"))
    assert len(db.session.scalars(db.select(Order)).all()) == 1
    assert len(db.session.scalars(db.select(OrderItem)).all()) == 1
    assert len(list(upload_folder.glob("*"))) == 1
    # 初期データも入っていない
    assert (
        db.session.scalar(db.select(User).filter_by(email="admin@example.com")) is None
    )


def test_seedは確認で答えれば実行される(app):
    result = run_seed(app, input="y\n")
    assert result.exit_code == 0
    assert len(db.session.scalars(db.select(Product)).all()) == 8


@pytest.mark.parametrize(("args", "input_text"), [(("--yes",), None), ((), "y\n")])
def test_seedはSQLite以外のデータベースでは実行できない(
    app, monkeypatch, customer, producer, make_product, args, input_text
):
    # 本番（MySQL）を想定する。--yes を付けても、確認に y と答えても止まる
    monkeypatch.setattr("marche.commands.is_sqlite", lambda: False)
    product = make_product(producer, name="消えてはいけない商品")
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    customer_email = customer.email
    product_name = product.name
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    db.session.expunge_all()

    result = run_seed(app, *args, input=input_text)

    assert result.exit_code != 0
    assert "SQLite" in result.output
    assert "よろしいですか" not in result.output  # 確認する前に止まる
    assert db.session.scalar(db.select(User).filter_by(email=customer_email))
    assert db.session.scalar(db.select(Product).filter_by(name=product_name))
    assert (
        db.session.scalar(db.select(User).filter_by(email="admin@example.com")) is None
    )
    assert len(list(upload_folder.glob("*"))) == 1


def test_seedは途中で失敗すると元のデータが残る(
    app, monkeypatch, customer, producer, make_product
):
    # 価格が CHECK 制約（50円以上）に違反する商品を混ぜて、投入の途中で失敗させる
    broken = [("producer1@example.com", "価格が不正な商品", 10, 1, True)]
    monkeypatch.setattr("marche.commands.SEED_PRODUCTS", broken)
    product = make_product(producer, name="消えてはいけない商品")
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    customer_email = customer.email
    product_name = product.name
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    db.session.expunge_all()

    result = run_seed(app, "--yes")

    assert result.exit_code != 0
    assert isinstance(result.exception, IntegrityError)
    # 削除も取り消されて、元のデータがそのまま残る
    assert db.session.scalar(db.select(User).filter_by(email=customer_email))
    assert db.session.scalar(db.select(Product).filter_by(name=product_name))
    assert (
        db.session.scalar(db.select(User).filter_by(email="admin@example.com")) is None
    )
    # 写真のファイルも消えていない
    assert len(list(upload_folder.glob("*"))) == 1


def test_seedのあと一覧に公開中の7商品が出る(app, client):
    run_seed(app, "--yes")
    text = client.get("/").text
    assert "八幡平マッシュルーム 200g" in text
    assert "1,200円" in text
    assert "売り切れ" in text
    assert "試作中の干し野菜セット" not in text
