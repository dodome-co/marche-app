import io
import re
from pathlib import Path

import pytest

from marche.extensions import db
from marche.models import Product
from marche.uploads import delete_image, detect_media_type, save_image

# 先頭のバイトだけを本物にした、テスト用の小さなデータ
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
WEBP = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
HTML = b"<html><script>alert(1)</script></html>"

MB = 1024 * 1024


def uploaded_files(app):
    folder = Path(app.config["UPLOAD_FOLDER"])
    return sorted(folder.glob("*")) if folder.exists() else []


def form_data(image=None, **overrides):
    data = {
        "name": "ほうれん草 1束",
        "price": "200",
        "stock": "10",
        "description": "朝採りです。",
        "is_published": "y",
    }
    data.update(overrides)
    if image is not None:
        content, filename = image
        data["image"] = (io.BytesIO(content), filename)
    return data


def post(client, url, **kwargs):
    return client.post(
        url, data=form_data(**kwargs), content_type="multipart/form-data"
    )


def only_product():
    return db.session.scalars(db.select(Product)).one()


# --- detect_media_type ---


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (WEBP, "image/webp"),
        (GIF, None),
        (HTML, None),
        (b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 8, None),
        (b"", None),
        (b"\xff\xd8", None),
    ],
)
def test_ファイルの種類を先頭のバイトで判定する(data, expected):
    assert detect_media_type(data) == expected


# --- save_image / delete_image ---


def test_写真はUUIDの名前で中身に合う拡張子で保存される(app):
    for data, extension in [(JPEG, ".jpg"), (PNG, ".png"), (WEBP, ".webp")]:
        filename = save_image(data, detect_media_type(data))
        assert re.fullmatch(rf"[0-9a-f]{{32}}\{extension}", filename)
        assert (Path(app.config["UPLOAD_FOLDER"]) / filename).read_bytes() == data


def test_写真を消せて_無くてもエラーにならない(app):
    filename = save_image(JPEG, "image/jpeg")
    delete_image(filename)
    assert uploaded_files(app) == []
    delete_image(filename)
    delete_image(None)
    delete_image("")


def test_フォルダーの外を指す名前は消さない(app, tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(JPEG)
    save_image(JPEG, "image/jpeg")
    delete_image("../outside.jpg")
    delete_image("..")
    assert outside.exists()
    assert len(uploaded_files(app)) == 1


# --- 登録・編集・削除 ---


def test_写真つきで登録できる(app, client, producer, login):
    login(producer)
    response = post(client, "/producer/products/new", image=(JPEG, "photo.jpg"))
    assert response.status_code == 302
    product = only_product()
    assert re.fullmatch(r"[0-9a-f]{32}\.jpg", product.image_filename)
    assert [f.name for f in uploaded_files(app)] == [product.image_filename]


@pytest.mark.parametrize(
    ("content", "filename", "extension"),
    [
        (PNG, "photo.png", ".png"),
        (WEBP, "photo.webp", ".webp"),
        (JPEG, "photo.JPEG", ".jpg"),
    ],
)
def test_PNGとWebPも登録できる(client, producer, login, content, filename, extension):
    login(producer)
    post(client, "/producer/products/new", image=(content, filename))
    assert only_product().image_filename.endswith(extension)


def test_写真なしでも登録できる(app, client, producer, login):
    login(producer)
    response = post(client, "/producer/products/new")
    assert response.status_code == 302
    assert only_product().image_filename is None
    assert uploaded_files(app) == []


def test_クライアントのファイル名は使われない(app, client, producer, login):
    login(producer)
    post(client, "/producer/products/new", image=(JPEG, "../../evil.png"))
    product = only_product()
    assert re.fullmatch(r"[0-9a-f]{32}\.jpg", product.image_filename)
    folder = Path(app.config["UPLOAD_FOLDER"])
    assert [f.name for f in uploaded_files(app)] == [product.image_filename]
    assert not (folder.parent / "evil.png").exists()
    assert not (folder.parent.parent / "evil.png").exists()


def test_写真を差し替えると古い写真が消える(app, client, producer, make_product, login):
    product = make_product(producer)
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    old = product.image_filename
    login(producer)
    response = post(
        client, f"/producer/products/{product.id}/edit", image=(PNG, "new.png")
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.image_filename != old
    assert product.image_filename.endswith(".png")
    assert [f.name for f in uploaded_files(app)] == [product.image_filename]


def test_写真を選ばずに編集すると今の写真が残る(
    app, client, producer, make_product, login
):
    product = make_product(producer)
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    old = product.image_filename
    login(producer)
    post(client, f"/producer/products/{product.id}/edit", name="名前だけ変更")
    db.session.refresh(product)
    assert product.name == "名前だけ変更"
    assert product.image_filename == old
    assert [f.name for f in uploaded_files(app)] == [old]


def test_編集画面に今の写真が出る(client, producer, make_product, login):
    product = make_product(producer, name="にんじん")
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    login(producer)
    response = client.get(f"/producer/products/{product.id}/edit")
    assert f"/static/uploads/{product.image_filename}" in response.text
    assert 'enctype="multipart/form-data"' in response.text


def test_商品を削除すると写真も消える(app, client, producer, make_product, login):
    product = make_product(producer)
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 302
    assert uploaded_files(app) == []


# --- 不正な入力 ---


@pytest.mark.parametrize(
    ("content", "filename"),
    [
        (GIF, "photo.gif"),
        (HTML, "photo.svg"),
        (b"text", "memo.txt"),
        (HTML, "photo.png"),  # 拡張子は画像だが、中身が画像ではない
        (b"", "empty.jpg"),
    ],
)
def test_画像でないファイルは登録できない(
    app, client, producer, login, content, filename
):
    login(producer)
    response = post(client, "/producer/products/new", image=(content, filename))
    assert response.status_code == 422
    assert db.session.scalars(db.select(Product)).all() == []
    assert uploaded_files(app) == []


def test_5MBを超える写真は登録できない(app, client, producer, login):
    login(producer)
    too_big = JPEG + b"\x00" * (5 * MB)
    response = post(client, "/producer/products/new", image=(too_big, "big.jpg"))
    assert response.status_code == 422
    assert "5MB" in response.text
    assert db.session.scalars(db.select(Product)).all() == []
    assert uploaded_files(app) == []


def test_5MBちょうどの写真は登録できる(client, producer, login):
    login(producer)
    exactly = JPEG + b"\x00" * (5 * MB - len(JPEG))
    response = post(client, "/producer/products/new", image=(exactly, "ok.jpg"))
    assert response.status_code == 302


def test_リクエスト全体が6MBを超えると413(client, producer, login):
    login(producer)
    huge = JPEG + b"\x00" * (7 * MB)
    response = post(client, "/producer/products/new", image=(huge, "huge.jpg"))
    assert response.status_code == 413
    assert "ファイルが大きすぎます" in response.text


def test_画像でない編集は古い写真を消さない(app, client, producer, make_product, login):
    product = make_product(producer, name="編集前")
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    old = product.image_filename
    login(producer)
    response = post(
        client,
        f"/producer/products/{product.id}/edit",
        name="編集後",
        image=(HTML, "evil.png"),
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.name == "編集前"
    assert product.image_filename == old
    assert [f.name for f in uploaded_files(app)] == [old]


def test_入力エラーのときは写真を保存しない(app, client, producer, login):
    login(producer)
    response = post(
        client, "/producer/products/new", price="49", image=(JPEG, "photo.jpg")
    )
    assert response.status_code == 422
    assert uploaded_files(app) == []


def test_画像のエラーは日本語で出る(client, producer, login):
    login(producer)
    response = post(client, "/producer/products/new", image=(HTML, "photo.png"))
    assert "JPEG・PNG・WebP の画像を選んでください" in response.text


# --- 権限なし ---


def test_他の生産者の商品に写真は付けられない(
    app, client, producer, other_producer, make_product, login
):
    product = make_product(other_producer)
    login(producer)
    response = post(
        client, f"/producer/products/{product.id}/edit", image=(JPEG, "photo.jpg")
    )
    assert response.status_code == 403
    db.session.refresh(product)
    assert product.image_filename is None
    assert uploaded_files(app) == []


def test_他の生産者の商品を削除しても写真は消えない(
    app, client, producer, other_producer, make_product, login
):
    product = make_product(other_producer)
    product.image_filename = save_image(JPEG, "image/jpeg")
    db.session.commit()
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 403
    assert len(uploaded_files(app)) == 1
