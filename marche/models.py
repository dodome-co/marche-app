from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from marche.extensions import db

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    """日本時間の現在時刻。SQLite はタイムゾーンを保存しないので tzinfo を外して使う"""
    return datetime.now(JST).replace(tzinfo=None)


class Role(StrEnum):
    CUSTOMER = "customer"
    PRODUCER = "producer"
    ADMIN = "admin"


ROLE_LABELS = {Role.CUSTOMER: "購入者", Role.PRODUCER: "生産者", Role.ADMIN: "管理者"}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.CUSTOMER)
    created_at: Mapped[datetime] = mapped_column(default=now_jst)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_producer(self) -> bool:
        return self.role == Role.PRODUCER

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def role_label(self) -> str:
        return ROLE_LABELS[Role(self.role)]
