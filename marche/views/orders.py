from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from marche.cart import clear_cart, get_cart
from marche.extensions import db
from marche.models import Order, OrderStatus
from marche.services.inventory import OrderError, create_order

bp = Blueprint("orders", __name__, url_prefix="/orders")


def get_own_order_or_403(order_id: int) -> Order:
    """自分の注文を返す。注文がなければ 404、本人でも管理者でもなければ 403"""
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return order


@bp.route("/", methods=["POST"])
@login_required
def create():
    # 金額はフォームからも受け取らない。create_order が DB の価格で計算する
    try:
        order = create_order(current_user, get_cart())
    except OrderError as error:
        flash(str(error))
        return redirect(url_for("cart.show"))
    clear_cart()
    flash("ご注文を受け付けました")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template(
        "orders/show.html",
        order=order,
        is_pending=order.status == OrderStatus.PENDING,
    )
