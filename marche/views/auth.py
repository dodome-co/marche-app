from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from marche.extensions import db, login_manager
from marche.forms import LoginForm, RegisterForm
from marche.models import User

bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


def home_url(user: User) -> str:
    """ログイン後の行き先。生産者の画面ができたら、ロールで行き先を分ける"""
    return url_for("shop.index")


def safe_next(target: str | None) -> str | None:
    # 外部サイトへ飛ばされないように、"/" で始まる自サイトのパスだけを許す
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


@bp.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if db.session.scalar(db.select(User).filter_by(email=email)):
            form.email.errors.append("このメールアドレスは登録済みです")
            return render_template("auth/register.html", form=form), 422
        user = User(name=form.name.data.strip(), email=email, role=form.role.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("登録しました")
        return redirect(home_url(user))
    status = 422 if form.is_submitted() else 200
    return render_template("auth/register.html", form=form), status


@bp.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            db.select(User).filter_by(email=form.email.data.strip().lower())
        )
        if user is None or not user.check_password(form.password.data):
            flash("メールアドレスかパスワードが違います")
            return render_template("auth/login.html", form=form), 422
        login_user(user)
        return redirect(safe_next(request.args.get("next")) or home_url(user))
    status = 422 if form.is_submitted() else 200
    return render_template("auth/login.html", form=form), status


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("ログアウトしました")
    return redirect(url_for("shop.index"))


@bp.app_context_processor
def inject_user():
    return {"current_user": current_user}
