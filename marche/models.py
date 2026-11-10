from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
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

    products: Mapped[list[Product]] = relationship(back_populates="producer")

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


class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 50 AND price <= 100000", name="ck_products_price"),
        CheckConstraint("stock >= 0 AND stock <= 999", name="ck_products_stock"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    producer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int]
    stock: Mapped[int] = mapped_column(default=0)
    is_published: Mapped[bool] = mapped_column(default=False)
    image_filename: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=now_jst)
    updated_at: Mapped[datetime] = mapped_column(default=now_jst, onupdate=now_jst)

    producer: Mapped[User] = relationship(back_populates="products")
