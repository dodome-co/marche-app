from flask import Blueprint, render_template

bp = Blueprint("shop", __name__)


@bp.route("/")
def index():
    # 公開中の商品の一覧は、Day4 の「一覧と詳細」の章で作る
    return render_template("shop/index.html")
