import pytest
from sqlalchemy.exc import IntegrityError

from marche.extensions import db
from marche.models import (
    ORDER_STATUS_LABELS,
    Order,
    OrderItem,
    OrderStatus,
)


def test_状態の値と日本語のラベル():
    assert [s.value for s in OrderStatus] == ["pending", "paid", "cancelled"]
    assert ORDER_STATUS_LABELS[OrderStatus.PENDING] == "支払い待ち"
    assert ORDER_STATUS_LABELS[OrderStatus.PAID] == "支払い済み"
    assert ORDER_STATUS_LABELS[OrderStatus.CANCELLED] == "キャンセル"
    assert set(ORDER_STATUS_LABELS) == set(OrderStatus)


@pytest.mark.parametrize(
    ("status", "label"),
    [
        (OrderStatus.PENDING, "支払い待ち"),
        (OrderStatus.PAID, "支払い済み"),
        (OrderStatus.CANCELLED, "キャンセル"),
    ],
)
def test_注文の状態のラベルが出せる(
    customer, producer, make_product, make_order, status, label
):
    product = make_product(producer)
    order = make_order(customer, [(product, 1)], status=status)
    assert order.status_label == label


def test_注文の初期値(customer, producer, make_product, make_order):
    product = make_product(producer)
    order = make_order(customer, [(product, 1)])
    assert order.status == OrderStatus.PENDING
    assert order.created_at is not None
    assert order.paid_at is None
    assert order.stripe_checkout_session_id is None
    assert order.stripe_payment_intent_id is None


def test_リレーションでたどれる(customer, producer, make_product, make_order):
    product = make_product(producer)
    order = make_order(customer, [(product, 2)])
    (item,) = order.items
    assert order.user is customer
    assert customer.orders == [order]
    assert item.order is order
    assert item.product is product
    assert product.order_items == [item]


def test_注文を消すと明細も消えて商品は残る(
    customer, producer, make_product, make_order
):
    product = make_product(producer)
    order = make_order(customer, [(product, 2)])
    db.session.delete(order)
    db.session.commit()
    assert db.session.scalars(db.select(OrderItem)).all() == []
    assert db.session.get(type(product), product.id) is not None


def test_注文から外した明細は消える(customer, producer, make_product, make_order):
    product = make_product(producer)
    order = make_order(customer, [(product, 2)])
    order.items.clear()
    db.session.commit()
    assert db.session.scalars(db.select(OrderItem)).all() == []


def test_明細は注文時点の商品名と単価を持つ(
    customer, producer, make_product, make_order
):
    product = make_product(producer, name="注文時の名前", price=200)
    order = make_order(customer, [(product, 3)])
    product.name = "あとで変えた名前"
    product.price = 999
    db.session.commit()
    db.session.refresh(order)
    (item,) = order.items
    assert item.product_name == "注文時の名前"
    assert item.unit_price == 200
    assert item.subtotal == 600
    assert order.total_amount == 600


def test_Checkout_SessionのIDは重複できない(
    customer, producer, make_product, make_order
):
    product = make_product(producer)
    first = make_order(customer, [(product, 1)])
    second = make_order(customer, [(product, 1)])
    first.stripe_checkout_session_id = "cs_test_same"
    db.session.commit()
    second.stripe_checkout_session_id = "cs_test_same"
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_Checkout_SessionのIDが未設定の注文は複数作れる(
    customer, producer, make_product, make_order
):
    product = make_product(producer)
    make_order(customer, [(product, 1)])
    make_order(customer, [(product, 1)])
    assert len(db.session.scalars(db.select(Order)).all()) == 2


def test_同じ注文に同じ商品の明細は2つ作れない(
    customer, producer, make_product, make_order
):
    product = make_product(producer)
    order = make_order(customer, [(product, 1)])
    order.items.append(
        OrderItem(
            product_id=product.id,
            product_name=product.name,
            unit_price=product.price,
            quantity=1,
            subtotal=product.price,
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [{"quantity": 0}, {"quantity": 11}, {"unit_price": 49}, {"unit_price": 100001}],
)
def test_明細の数量と単価の範囲(
    customer, producer, make_product, make_order, overrides
):
    product = make_product(producer)
    order = make_order(customer, [])
    values = {
        "product_id": product.id,
        "product_name": product.name,
        "unit_price": 200,
        "quantity": 1,
        "subtotal": 200,
    } | overrides
    order.items.append(OrderItem(**values))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_合計金額は0未満にできない(customer):
    db.session.add(Order(user_id=customer.id, total_amount=-1))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
