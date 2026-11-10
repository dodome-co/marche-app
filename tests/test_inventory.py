from datetime import timedelta

import pytest
from sqlalchemy import event

from marche.extensions import db
from marche.models import OrderStatus, now_jst
from marche.services.inventory import (
    MAX_QUANTITY,
    PENDING_MINUTES,
    available_stock,
    purchase_limit,
    reserved_quantity,
)

# 注文は conftest の make_order で直接作る（注文を作る処理 create_order は通さない）


def minutes_ago(minutes):
    return now_jst() - timedelta(minutes=minutes)


def test_押さえる時間は35分():
    assert PENDING_MINUTES == 35


def test_注文がなければ在庫がそのまま買える(producer, make_product):
    product = make_product(producer, stock=10)
    assert reserved_quantity(product) == 0
    assert available_stock(product) == 10


def test_支払い待ちの注文の数量が引かれる(producer, customer, make_product, make_order):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 3)])
    assert reserved_quantity(product) == 3
    assert available_stock(product) == 7


def test_複数の注文の数量が合計される(producer, customer, make_product, make_order):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 3)])
    make_order(customer, [(product, 2)])
    assert reserved_quantity(product) == 5
    assert available_stock(product) == 5


def test_35分以内の支払い待ちは引かれる(producer, customer, make_product, make_order):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 4)], created_at=minutes_ago(PENDING_MINUTES - 1))
    assert available_stock(product) == 6


def test_35分を過ぎた支払い待ちは引かれない(
    producer, customer, make_product, make_order
):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 4)], created_at=minutes_ago(PENDING_MINUTES + 1))
    assert reserved_quantity(product) == 0
    assert available_stock(product) == 10


def test_支払い済みとキャンセルの注文は引かれない(
    producer, customer, make_product, make_order
):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 3)], status=OrderStatus.PAID)
    make_order(customer, [(product, 2)], status=OrderStatus.CANCELLED)
    assert reserved_quantity(product) == 0
    assert available_stock(product) == 10


def test_別の商品の注文は引かれない(producer, customer, make_product, make_order):
    carrot = make_product(producer, name="にんじん", stock=10)
    radish = make_product(producer, name="だいこん", stock=10)
    make_order(customer, [(radish, 5)])
    assert available_stock(carrot) == 10
    assert available_stock(radish) == 5


def test_1つの注文に複数の商品があっても商品ごとに数える(
    producer, customer, make_product, make_order
):
    carrot = make_product(producer, name="にんじん", stock=10)
    radish = make_product(producer, name="だいこん", stock=10)
    make_order(customer, [(carrot, 2), (radish, 6)])
    assert available_stock(carrot) == 8
    assert available_stock(radish) == 4


def test_期限切れと期限内の支払い待ちが混ざると期限内だけ引かれる(
    producer, customer, make_product, make_order
):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 4)], created_at=minutes_ago(PENDING_MINUTES + 1))
    make_order(customer, [(product, 2)], created_at=minutes_ago(PENDING_MINUTES - 1))
    assert reserved_quantity(product) == 2
    assert available_stock(product) == 8


def test_取り消した注文が混ざっても支払い待ちだけ引かれる(
    producer, customer, make_product, make_order
):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 3)])
    make_order(customer, [(product, 5)], status=OrderStatus.CANCELLED)
    assert available_stock(product) == 7


def test_押さえた数が在庫を超えても0未満にならない(
    producer, customer, make_product, make_order
):
    product = make_product(producer, stock=2)
    make_order(customer, [(product, 5)])
    assert reserved_quantity(product) == 5
    assert available_stock(product) == 0


def test_在庫0は0のまま(producer, make_product):
    product = make_product(producer, stock=0)
    assert available_stock(product) == 0


def test_押さえた数はint型で返る(producer, customer, make_product, make_order):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 3)])
    assert type(reserved_quantity(product)) is int
    assert type(available_stock(product)) is int


def test_押さえた数はSQLを1回で数える(producer, customer, make_product, make_order):
    product = make_product(producer, stock=100)
    for quantity in (1, 2, 3):
        make_order(customer, [(product, quantity)])
    # コミットで期限切れになった商品を先に読み込み、数えるのは在庫の SQL だけにする
    db.session.refresh(product)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        assert reserved_quantity(product) == 6
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert len(statements) == 1
    assert "sum(" in statements[0].lower()


@pytest.mark.parametrize(
    ("stock", "reserved", "expected"),
    [(50, 0, MAX_QUANTITY), (3, 0, 3), (5, 3, 2), (10, 0, 10), (12, 1, 10), (0, 0, 0)],
)
def test_1回に買える数はmin_10_買える数(
    producer, customer, make_product, make_order, stock, reserved, expected
):
    product = make_product(producer, stock=stock)
    if reserved:
        make_order(customer, [(product, reserved)])
    assert purchase_limit(product) == expected
