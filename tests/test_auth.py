import pytest

from marche import create_app
from marche.extensions import db
from marche.models import Role, User


def register(client, role):
    return client.post(
        "/register",
        data={
            "name": "高橋",
            "email": "t@example.com",
            "password": "password123",
            "role": role,
        },
    )


def test_トップページが表示される(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "はちまん産直マルシェ" in response.text


def test_生産者として登録できる(client):
    response = register(client, "producer")
    assert response.status_code == 302
    user = db.session.scalar(db.select(User).filter_by(email="t@example.com"))
    assert user.role == Role.PRODUCER
    assert user.password_hash != "password123"  # パスワードはハッシュで保存する


def test_管理者は登録画面から選べない(client):
    response = register(client, "admin")
    assert response.status_code == 422
    assert db.session.scalar(db.select(User).filter_by(email="t@example.com")) is None


def test_同じメールアドレスでは登録できない(client, customer):
    response = client.post(
        "/register",
        data={
            "name": "別の人",
            "email": customer.email,
            "password": "password123",
            "role": "customer",
        },
    )
    assert response.status_code == 422
    assert "登録済み" in response.text


def test_ログインとログアウトができる(client, customer, login):
    response = login(customer)
    assert response.status_code == 302
    assert "購入者さん" in client.get("/").text
    client.post("/logout")
    assert "ログイン" in client.get("/").text


def test_パスワードが違うとログインできない(client, customer):
    response = client.post(
        "/login", data={"email": customer.email, "password": "wrong-password"}
    )
    assert response.status_code == 422


def test_ログイン後のnextに外部URLは使えない(client, customer):
    response = client.post(
        "/login?next=https://evil.example.com/",
        data={"email": customer.email, "password": "password"},
    )
    assert response.headers["Location"] == "/"


def test_create_adminで管理者を作れる(app):
    result = app.test_cli_runner().invoke(
        args=[
            "create-admin",
            "--email",
            "boss@example.com",
            "--name",
            "管理者",
            "--password",
            "longpassword",
        ]
    )
    assert result.exit_code == 0
    user = db.session.scalar(db.select(User).filter_by(email="boss@example.com"))
    assert user.role == Role.ADMIN


def test_本番でSECRET_KEYが無いと起動しない(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    with pytest.raises(RuntimeError):
        create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
