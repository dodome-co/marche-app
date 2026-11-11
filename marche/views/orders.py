from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from marche.cart import clear_cart, get_cart
from marche.extensions import db
from marche.models import Order, OrderStatus
from marche.services import inventory
from marche.services.inventory import OrderError, create_order
from marche.services.payment import PaymentError

bp = Blueprint("orders", __name__, url_prefix="/orders")


def get_own_order_or_403(order_id: int) -> Order:
    """自分の注文を返す。注文がなければ 404、本人でも管理者でもなければ 403"""
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return order


def discard_unsaved_session(payment, order: Order, session_id: str) -> None:
    """注文に保存できなかった Session を期限切れにして、注文をキャンセルする"""
    try:
        payment.expire_session(session_id)
    except PaymentError:
        # 期限切れにできなくても、注文はキャンセルする。Session は31分で自然に期限切れになる
        current_app.logger.warning(
            "保存できなかった Session を期限切れにできませんでした (order=%s)", order.id
        )
    inventory.cancel(order)


@bp.route("/", methods=["POST"])
@login_required
def create():
    # 金額はフォームからも受け取らない。create_order が DB の価格で計算する
    try:
        order = create_order(current_user, get_cart())
    except OrderError as error:
        flash(str(error))
        return redirect(url_for("cart.show"))
    payment = current_app.extensions["payment"]
    try:
        session_id, url = payment.create_checkout_session(order)
    except PaymentError as error:
        # 決済ページを作れなかったので、作った注文をキャンセルしてカートに戻す
        inventory.cancel(order)
        flash(str(error))
        return redirect(url_for("cart.show"))
    order.stripe_checkout_session_id = session_id
    try:
        db.session.commit()
    except Exception:
        # Session は作れたのに、注文に ID を保存できなかった。
        # 払える Session が残ると、支払われても注文を確定できないので、後始末をする
        db.session.rollback()
        current_app.logger.exception("Session の ID を注文に保存できませんでした")
        discard_unsaved_session(payment, order, session_id)
        flash("注文を完了できませんでした。時間をおいてもう一度お試しください")
        return redirect(url_for("cart.show"))
    clear_cart()
    return redirect(url, code=303)


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template(
        "orders/show.html",
        order=order,
        is_pending=order.status == OrderStatus.PENDING,
    )


@bp.route("/<int:order_id>/complete")
@login_required
def complete(order_id: int):
    # 仮の画面。支払いの確定（Stripe への問い合わせ、在庫の減算）は次の章で作る。
    # ここでは注文の状態を変えない
    order = get_own_order_or_403(order_id)
    return render_template("orders/complete.html", order=order)


@bp.route("/<int:order_id>/cancel", methods=["GET", "POST"])
@login_required
def cancel(order_id: int):
    order = get_own_order_or_403(order_id)
    if request.method == "GET":
        return render_template(
            "orders/cancel.html",
            order=order,
            can_cancel=order.status == OrderStatus.PENDING,
        )
    detail = redirect(url_for("orders.show", order_id=order.id))
    if order.status != OrderStatus.PENDING:
        flash("この注文はキャンセルできません")
        return detail
    if order.stripe_checkout_session_id:
        # 先に Session を期限切れにする。失敗したら（すでに支払い済みなど）取り消さない
        try:
            current_app.extensions["payment"].expire_session(
                order.stripe_checkout_session_id
            )
        except PaymentError as error:
            flash(str(error))
            return detail
    if inventory.cancel(order):
        flash("注文をキャンセルしました")
    else:
        flash("この注文はキャンセルできません")
    return detail
