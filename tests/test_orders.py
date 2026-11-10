from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from marche.extensions import db
from marche.models import (
    ORDER_STATUS_LABELS,
    Order,
    OrderItem,
    OrderStatus,
    now_jst,
)
from marche.services.inventory import (
    MAX_QUANTITY,
    PENDING_MINUTES,
    OrderError,
    available_stock,
    create_order,
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


# --- create_order（注文を作る処理） ---


def assert_nothing_created():
    """注文も明細も作られず、セッションにも作りかけが残っていない"""
    assert db.session.scalars(db.select(Order)).all() == []
    assert db.session.scalars(db.select(OrderItem)).all() == []
    assert not db.session.new


def test_最大の数量は10():
    assert MAX_QUANTITY == 10


def test_合計がDBの価格と数量から計算される(customer, producer, make_product):
    carrot = make_product(
        producer, name="にんじん", price=200, stock=10, is_published=True
    )
    pork = make_product(producer, name="ポーク", price=1200, stock=5, is_published=True)
    order = create_order(customer, {carrot.id: 3, pork.id: 2})
    assert order.total_amount == 200 * 3 + 1200 * 2
    items = sorted(order.items, key=lambda item: item.product_id)
    assert [(i.product_name, i.unit_price, i.quantity, i.subtotal) for i in items] == [
        ("にんじん", 200, 3, 600),
        ("ポーク", 1200, 2, 2400),
    ]
    assert order.total_amount == sum(item.subtotal for item in order.items)


def test_作られた注文の中身(customer, producer, make_product):
    product = make_product(
        producer, name="にんじん", price=450, stock=10, is_published=True
    )
    order = create_order(customer, {product.id: 2})
    db.session.expire_all()  # DB に保存された値を読み直す
    saved = db.session.get(Order, order.id)
    assert saved.user_id == customer.id
    assert saved.status == OrderStatus.PENDING
    assert saved.total_amount == 900
    assert saved.stripe_checkout_session_id is None
    assert saved.paid_at is None
    (item,) = saved.items
    assert item.product_id == product.id
    assert (item.product_name, item.unit_price, item.quantity, item.subtotal) == (
        "にんじん",
        450,
        2,
        900,
    )


def test_注文を作っても在庫は減らず買える数だけ減る(customer, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    create_order(customer, {product.id: 3})
    db.session.refresh(product)
    assert product.stock == 10  # 減らすのは、支払いが確定したとき
    assert available_stock(product) == 7


def test_commitは1回だけ(customer, producer, make_product):
    carrot = make_product(producer, name="にんじん", stock=10, is_published=True)
    radish = make_product(producer, name="だいこん", stock=10, is_published=True)
    commits = []

    def count(session):
        commits.append(1)

    event.listen(db.session, "after_commit", count)
    try:
        create_order(customer, {carrot.id: 1, radish.id: 1})
    finally:
        event.remove(db.session, "after_commit", count)
    assert len(commits) == 1


@pytest.mark.parametrize("quantity", [1, MAX_QUANTITY])
def test_数量の境界は注文できる(customer, producer, make_product, quantity):
    product = make_product(producer, stock=10, is_published=True)
    order = create_order(customer, {product.id: quantity})
    assert order.items[0].quantity == quantity


def test_セッション由来の文字列のキーでも注文できる(customer, producer, make_product):
    product = make_product(producer, price=300, stock=10, is_published=True)
    order = create_order(customer, {str(product.id): 2})
    assert order.total_amount == 600


def test_買える数ちょうどなら注文できる(customer, producer, make_product):
    product = make_product(producer, stock=4, is_published=True)
    create_order(customer, {product.id: 4})
    assert available_stock(product) == 0


def test_35分を過ぎた支払い待ちは押さえに数えない(
    customer, other_producer, producer, make_product, make_order
):
    product = make_product(producer, stock=5, is_published=True)
    old = now_jst() - timedelta(minutes=PENDING_MINUTES + 1)
    make_order(other_producer, [(product, 5)], created_at=old)
    order = create_order(customer, {product.id: 5})
    assert order.total_amount == product.price * 5


# --- create_order: 在庫と非公開 ---


def test_在庫を超えるとOrderErrorで注文が作られない(customer, producer, make_product):
    product = make_product(producer, name="にんじん", stock=3, is_published=True)
    with pytest.raises(OrderError) as error:
        create_order(customer, {product.id: 4})
    assert "「にんじん」の在庫が足りません" in str(error.value)
    assert "いま買える数: 3" in str(error.value)
    assert_nothing_created()


def test_売り切れはOrderError(customer, producer, make_product):
    product = make_product(producer, name="ピクルス", stock=0, is_published=True)
    with pytest.raises(OrderError) as error:
        create_order(customer, {product.id: 1})
    assert str(error.value) == "「ピクルス」は売り切れです"
    assert_nothing_created()


def test_他の人の支払い待ちが押さえた分は買えない(
    customer, other_producer, producer, make_product, make_order
):
    product = make_product(producer, stock=5, is_published=True)
    make_order(other_producer, [(product, 3)])
    with pytest.raises(OrderError) as error:
        create_order(customer, {product.id: 3})
    assert "いま買える数: 2" in str(error.value)
    assert len(db.session.scalars(db.select(Order)).all()) == 1  # 最初の注文だけ


def test_非公開の商品はOrderError(customer, producer, make_product):
    product = make_product(producer, name="試作中の商品", stock=10, is_published=False)
    with pytest.raises(OrderError) as error:
        create_order(customer, {product.id: 1})
    assert "購入できない商品" in str(error.value)
    assert "試作中の商品" not in str(
        error.value
    )  # 非公開の商品が「ある」ことを教えない
    assert_nothing_created()


def test_存在しない商品はOrderError(customer):
    with pytest.raises(OrderError) as error:
        create_order(customer, {9999: 1})
    assert "購入できない商品" in str(error.value)
    assert_nothing_created()


def test_一部だけ駄目なら何も作られない_非公開(customer, producer, make_product):
    ok = make_product(producer, name="にんじん", stock=10, is_published=True)
    hidden = make_product(producer, name="だいこん", stock=10, is_published=False)
    with pytest.raises(OrderError):
        create_order(customer, {ok.id: 1, hidden.id: 1})
    assert_nothing_created()


def test_一部だけ駄目なら何も作られない_在庫不足(customer, producer, make_product):
    ok = make_product(producer, name="にんじん", stock=10, is_published=True)
    short = make_product(producer, name="だいこん", stock=1, is_published=True)
    with pytest.raises(OrderError) as error:
        create_order(customer, {ok.id: 1, short.id: 2})
    assert "「だいこん」" in str(error.value)  # 駄目な商品の名前が出る
    assert_nothing_created()


# --- create_order: 不正な入力 ---


def test_空のカートはOrderError(customer):
    with pytest.raises(OrderError) as error:
        create_order(customer, {})
    assert str(error.value) == "カートに商品がありません"
    assert_nothing_created()


@pytest.mark.parametrize("quantity", [0, -1, MAX_QUANTITY + 1, 100])
def test_数量が範囲外ならOrderError(customer, producer, make_product, quantity):
    product = make_product(producer, name="にんじん", stock=999, is_published=True)
    with pytest.raises(OrderError) as error:
        create_order(customer, {product.id: quantity})
    assert "「にんじん」の数量は1〜10で指定してください" in str(error.value)
    assert_nothing_created()


@pytest.mark.parametrize(
    "quantity", ["2", 2.5, True, None, {"price": 1, "quantity": 1}, [2]]
)
def test_数量が整数でないとOrderError(customer, producer, make_product, quantity):
    # 金額などを混ぜた値も、ここで弾かれる（金額はカートから受け取らない）
    product = make_product(producer, stock=10, is_published=True)
    with pytest.raises(OrderError):
        create_order(customer, {product.id: quantity})
    assert_nothing_created()


@pytest.mark.parametrize("key", ["abc", "", None, 1.5])
def test_商品IDが数字でないとOrderError(customer, key):
    with pytest.raises(OrderError):
        create_order(customer, {key: 1})
    assert_nothing_created()


def test_同じ商品が2回入っているとOrderError(customer, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    with pytest.raises(OrderError):
        create_order(customer, {product.id: 1, str(product.id): 1})
    assert_nothing_created()
