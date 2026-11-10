from datetime import timedelta

from sqlalchemy import func

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst

# 支払い待ちの注文が、在庫を押さえておく時間（分）。
# Stripe の Checkout Session の期限（最短30分）より少し長くしてある
PENDING_MINUTES = 35


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
