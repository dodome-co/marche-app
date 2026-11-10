from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from marche.decorators import role_required
from marche.extensions import db
from marche.forms import ProductForm
from marche.models import Product, Role
from marche.uploads import delete_image, detect_media_type, save_image

bp = Blueprint("producer", __name__, url_prefix="/producer")


def get_own_product_or_403(product_id: int) -> Product:
    """自分の商品を返す。商品がなければ 404、他の生産者の商品なら 403"""
    product = db.get_or_404(Product, product_id)
    if product.producer_id != current_user.id:
        abort(403)
    return product


def save_uploaded_image(form: ProductForm) -> str | None:
    """選ばれた写真を保存してファイル名を返す。選ばれていなければ None。

    中身の確認はフォームの検証（ProductForm.validate_image）で済んでいる。
    populate_obj が写真を商品に入れないように、ここでフォームから外す。
    """
    image = form.image.data
    del form.image
    if not image:
        return None
    data = image.read()
    return save_image(data, detect_media_type(data))


def commit_or_discard(filename: str | None) -> None:
    """コミットする。失敗したら、保存したばかりの写真を消してから例外を投げ直す"""
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        delete_image(filename)
        raise


@bp.route("/products")
@role_required(Role.PRODUCER)
def index():
    products = db.session.scalars(
        db.select(Product)
        .where(Product.producer_id == current_user.id)
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    return render_template("producer/index.html", products=products)


@bp.route("/products/new", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def new():
    form = ProductForm()
    if form.validate_on_submit():
        product = Product(producer_id=current_user.id)
        new_filename = save_uploaded_image(form)
        form.populate_obj(product)
        product.image_filename = new_filename
        db.session.add(product)
        commit_or_discard(new_filename)
        flash("商品を登録しました")
        return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template(
        "producer/form.html",
        form=form,
        product=None,
        title="商品の登録",
        submit_label="登録する",
    ), status


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def edit(product_id: int):
    product = get_own_product_or_403(product_id)
    form = ProductForm(obj=product)
    if form.validate_on_submit():
        old_filename = product.image_filename
        new_filename = save_uploaded_image(form)
        form.populate_obj(product)
        if new_filename:
            product.image_filename = new_filename
        commit_or_discard(new_filename)
        if new_filename:
            delete_image(old_filename)  # 差し替えたら、古い写真を消す
        flash("商品を更新しました")
        return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template(
        "producer/form.html",
        form=form,
        product=product,
        title="商品の編集",
        submit_label="保存する",
    ), status


@bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@role_required(Role.PRODUCER)
def toggle(product_id: int):
    product = get_own_product_or_403(product_id)
    product.is_published = not product.is_published
    db.session.commit()
    flash("公開しました" if product.is_published else "非公開にしました")
    return redirect(url_for("producer.index"))


@bp.route("/products/<int:product_id>/delete", methods=["POST"])
@role_required(Role.PRODUCER)
def delete(product_id: int):
    product = get_own_product_or_403(product_id)
    # 注文の明細（order_items）を作ったら、注文がある商品は削除できないようにする
    filename = product.image_filename
    db.session.delete(product)
    db.session.commit()
    delete_image(filename)  # DB から消えたあとで、写真のファイルを消す
    flash("商品を削除しました")
    return redirect(url_for("producer.index"))
