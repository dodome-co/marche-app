from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    session,
    url_for,
)

from marche.cart import CART_KEY, get_cart, save_cart, to_session
from marche.extensions import db
from marche.forms import CartForm
from marche.models import Product
from marche.services.inventory import purchase_limit

bp = Blueprint("cart", __name__, url_prefix="/cart")


def form_error(form: CartForm) -> str:
    return form.quantity.errors[0] if form.quantity.errors else "数量を入力してください"


@bp.route("/")
def show():
    cart = get_cart()
    products = {}
    if cart:
        products = {
            product.id: product
            for product in db.session.scalars(
                db.select(Product).where(Product.id.in_(list(cart)))
            )
        }
    lines = []
    valid_cart = {}
    for product_id, quantity in cart.items():
        product = products.get(product_id)
        if product is None or not product.is_published:
            continue  # 非公開になった商品、なくなった商品は表示しない
        valid_cart[product_id] = quantity
        lines.append(
            {
                "product": product,
                "quantity": quantity,
                "subtotal": product.price * quantity,  # 表示のためだけの計算
                "limit": purchase_limit(product),
            }
        )
    if valid_cart != cart:
        # 見えない商品がカートに残ると、注文のたびに断られて直せなくなるので、外して知らせる
        save_cart(valid_cart)
        flash("購入できなくなった商品をカートから外しました")
    elif session.get(CART_KEY, {}) != to_session(valid_cart):
        # get_cart が捨てた壊れた値がセッションに残っていたら、知らせずに消す
        save_cart(valid_cart)
    total = sum(line["subtotal"] for line in lines)
    return render_template("cart/show.html", lines=lines, total=total)


@bp.route("/add/<int:product_id>", methods=["POST"])
def add(product_id: int):
    product = db.get_or_404(Product, product_id)
    if not product.is_published:
        abort(404)
    back = redirect(url_for("shop.show", product_id=product.id))
    form = CartForm()
    if not form.validate_on_submit():
        flash(form_error(form))
        return back
    quantity = form.quantity.data
    if quantity < 1:
        flash("数量は1以上で指定してください")
        return back
    limit = purchase_limit(product)
    if limit <= 0:
        flash(f"「{product.name}」は売り切れです")
        return back
    cart = get_cart()
    in_cart = cart.get(product_id, 0)
    if in_cart + quantity > limit:
        if in_cart:
            flash(
                f"「{product.name}」はカートに{in_cart}個入っています。"
                f"合計で{limit}個まで買えます"
            )
        else:
            flash(f"「{product.name}」は{limit}個まで買えます")
        return back
    cart[product_id] = in_cart + quantity
    save_cart(cart)
    flash("カートに入れました")
    return redirect(url_for("cart.show"))


@bp.route("/update/<int:product_id>", methods=["POST"])
def update(product_id: int):
    back = redirect(url_for("cart.show"))
    cart = get_cart()
    if product_id not in cart:
        flash("カートに入っていない商品です")
        return back
    form = CartForm()
    if not form.validate_on_submit():
        flash(form_error(form))
        return back
    quantity = form.quantity.data
    if quantity <= 0:
        del cart[product_id]
        save_cart(cart)
        flash("カートから外しました")
        return back
    product = db.session.get(Product, product_id)
    if product is None or not product.is_published:
        del cart[product_id]
        save_cart(cart)
        flash("購入できなくなった商品をカートから外しました")
        return back
    limit = purchase_limit(product)
    if quantity > limit:
        if limit <= 0:
            flash(f"「{product.name}」は売り切れです")
        else:
            flash(f"「{product.name}」は{limit}個まで買えます")
        return back
    cart[product_id] = quantity
    save_cart(cart)
    flash("数量を変えました")
    return back
