import uuid
from pathlib import Path

from flask import current_app

# 保存するファイルの拡張子は、アップロードされた名前ではなく、中身の種類から決める
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def detect_media_type(data: bytes) -> str | None:
    """ファイルの先頭のバイト（マジックナンバー）で、JPEG / PNG / WebP を判定する"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save_image(data: bytes, media_type: str) -> str:
    """UUID のファイル名で保存して、そのファイル名を返す"""
    folder = Path(current_app.config["UPLOAD_FOLDER"])
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{EXTENSIONS[media_type]}"
    (folder / filename).write_bytes(data)
    return filename


def delete_image(filename: str | None) -> None:
    """写真を消す。ファイルが無くてもエラーにしない"""
    # 名前はサーバーが付けた値だが、念のため、フォルダーの外を指す名前は扱わない
    if not filename or filename in {".", ".."} or filename != Path(filename).name:
        return
    (Path(current_app.config["UPLOAD_FOLDER"]) / filename).unlink(missing_ok=True)
