from datetime import timedelta

from sqlalchemy import func

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, User, now_jst

# 支払い待ちの注文が、在庫を押さえておく時間（分）。
# Stripe の Checkout Session の期限（最短30分）より少し長くしてある
PENDING_MINUTES = 35

# 1商品あたり1回に買える数。order_items の CHECK 制約（1〜10）と同じ値にする
MAX_QUANTITY = 10

# 1人が同時に持てる支払い待ちの注文の数。
# 上限が無いと、払わない注文を繰り返して、全商品の在庫を押さえ続けられてしまう
MAX_PENDING_ORDERS = 1

# 非公開の商品と存在しない商品は同じメッセージにして、非公開の商品が「ある」ことを教えない
UNAVAILABLE_MESSAGE = "購入できない商品がカートに入っています"
INVALID_CART_MESSAGE = "カートの内容が正しくありません"


class OrderError(Exception):
    """注文を作れないときの例外。str(error) は、画面にそのまま出せる日本語のメッセージ"""


class PendingOrderError(OrderError):
    """すでに支払い待ちの注文があって、新しい注文を作れないとき。order が、その注文"""

    def __init__(self, order: Order):
        self.order = order
        super().__init__(
            f"お支払いが済んでいない注文（注文 {order.id}）があります。"
            "お支払いを済ませるか、取り消してから、もう一度ご注文ください"
        )


def reserved_quantity(product: Product) -> int:
    """支払い待ちで、作成から PENDING_MINUTES 分以内の注文の、この商品の数量の合計"""
    since = now_jst() - timedelta(minutes=PENDING_MINUTES)
    total = db.session.scalar(
        db.select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            OrderItem.product_id == product.id,
            Order.status == OrderStatus.PENDING,
            Order.created_at >= since,
        )
    )
    # MySQL の SUM は Decimal で返ることがあるので、int にそろえる
    return int(total)


def available_stock(product: Product) -> int:
    """いま買える数。在庫から、支払い待ちで押さえられている分を引く（0 未満にしない）"""
    return max(product.stock - reserved_quantity(product), 0)


def _normalize_cart(cart: dict) -> dict[int, int]:
    """カートを {商品ID(int): 数量(int)} にそろえる。形がおかしければ OrderError"""
    if not cart:
        raise OrderError("カートに商品がありません")
    items: dict[int, int] = {}
    for key, quantity in cart.items():
        # Flask のセッションは JSON で保存するので、商品ID のキーは文字列で戻ってくる
        if isinstance(key, bool) or not isinstance(key, int | str):
            raise OrderError(INVALID_CART_MESSAGE)
        try:
            product_id = int(key)
        except ValueError:
            raise OrderError(INVALID_CART_MESSAGE) from None
        # 数量は整数だけ。bool は int の仲間なので除く。金額などを混ぜた値もここで弾く
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise OrderError(INVALID_CART_MESSAGE)
        if product_id in items:  # 3 と "3" のように、同じ商品が2回入っている
            raise OrderError(INVALID_CART_MESSAGE)
        items[product_id] = quantity
    return items


def create_order(user: User, cart: dict) -> Order:
    """カート（{商品ID: 数量}）から、支払い待ちの注文を作る。

    先に全ての商品を確認し、1つでも駄目なら OrderError を投げる。
    そのときは DB にもセッションにも何も追加しない。確認が全部通ったら、1回だけ commit する。
    単価と商品名は、DB の商品から取る。金額は引数で受け取らない。
    在庫はここでは減らさない（支払いが確定したときに減らす）。
    """
    items = _normalize_cart(cart)

    # すでに支払い待ち（在庫を押さえている35分以内）の注文があれば、新しく作らせない
    since = now_jst() - timedelta(minutes=PENDING_MINUTES)
    pending = db.session.scalars(
        db.select(Order)
        .where(
            Order.user_id == user.id,
            Order.status == OrderStatus.PENDING,
            Order.created_at >= since,
        )
        .order_by(Order.id)
        .limit(MAX_PENDING_ORDERS)
    ).all()
    if len(pending) >= MAX_PENDING_ORDERS:
        raise PendingOrderError(pending[0])

    products = {
        product.id: product
        for product in db.session.scalars(
            db.select(Product).where(Product.id.in_(list(items)))
        )
    }
    lines: list[tuple[Product, int]] = []
    for product_id, quantity in items.items():
        product = products.get(product_id)
        if product is None or not product.is_published:
            raise OrderError(UNAVAILABLE_MESSAGE)
        if not 1 <= quantity <= MAX_QUANTITY:
            raise OrderError(
                f"「{product.name}」の数量は1〜{MAX_QUANTITY}で指定してください"
            )
        available = available_stock(product)
        if available <= 0:
            raise OrderError(f"「{product.name}」は売り切れです")
        if quantity > available:
            raise OrderError(
                f"「{product.name}」の在庫が足りません（いま買える数: {available}）"
            )
        lines.append((product, quantity))

    # ここから先は、確認が全部通ったときだけ
    order = Order(user_id=user.id, status=OrderStatus.PENDING, total_amount=0)
    for product, quantity in lines:
        subtotal = product.price * quantity
        order.items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                unit_price=product.price,
                quantity=quantity,
                subtotal=subtotal,
            )
        )
        order.total_amount += subtotal
    db.session.add(order)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return order


def purchase_limit(product: Product) -> int:
    """1回に買える数の上限。min(MAX_QUANTITY, いま買える数)。

    商品詳細の数量の選択肢、カートへの追加・変更の確認で、同じ値を使う。
    """
    return min(MAX_QUANTITY, available_stock(product))


def cancel(order: Order) -> bool:
    """支払い待ちの注文をキャンセルする。キャンセルできたら True。

    「読んで、pending ならキャンセルして保存」と2段階にせず、1つの UPDATE にする。
    Webhook や完了ページと同時に来ても、支払い済みの注文をキャンセルにしないため。
    """
    result = db.session.execute(
        db.update(Order)
        .where(Order.id == order.id, Order.status == OrderStatus.PENDING)
        .values(status=OrderStatus.CANCELLED)
    )
    db.session.commit()
    db.session.refresh(order)
    return result.rowcount == 1
