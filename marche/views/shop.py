from flask import Blueprint, abort, render_template
from flask_login import current_user
from sqlalchemy.orm import joinedload

from marche.extensions import db
from marche.models import Product
from marche.services.inventory import available_stock, purchase_limit

bp = Blueprint("shop", __name__)


def can_view(product: Product) -> bool:
    """公開中なら誰でも。非公開は、出品した生産者と管理者だけ"""
    if product.is_published:
        return True
    if not current_user.is_authenticated:
        return False
    return current_user.is_admin or product.producer_id == current_user.id


@bp.route("/")
def index():
    products = db.session.scalars(
        db.select(Product)
        .options(joinedload(Product.producer))
        .where(Product.is_published.is_(True))
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    stocks = {product.id: available_stock(product) for product in products}
    return render_template("shop/index.html", products=products, stocks=stocks)


@bp.route("/products/<int:product_id>")
def show(product_id: int):
    product = db.get_or_404(Product, product_id)
    if not can_view(product):
        abort(404)  # 403 ではなく 404。非公開の商品が「ある」ことも教えない
    return render_template(
        "shop/show.html",
        product=product,
        stock=available_stock(product),
        max_quantity=purchase_limit(product),  # カートに入れる数量の選択肢の上限
    )
