from flask import Blueprint, render_template

from marche.decorators import role_required
from marche.models import Role

bp = Blueprint("producer", __name__, url_prefix="/producer")


@bp.route("/products")
@role_required(Role.PRODUCER)
def index():
    # 出品管理の本体は、Day4 の「商品の登録・編集」の章で作る
    return render_template("producer/index.html")
