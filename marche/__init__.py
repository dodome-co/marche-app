import logging
import os
from datetime import datetime

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from marche.extensions import csrf, db, login_manager, migrate

WEEKDAYS = "月火水木金土日"  # datetime.weekday() は月曜が 0


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or "dev",
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", "sqlite:///marche.sqlite"
        ),
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        STRIPE_SECRET_KEY=os.environ.get("STRIPE_SECRET_KEY", ""),
        STRIPE_WEBHOOK_SECRET=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
        # テストでフェイクを渡して差し替える。None なら本物（services/payment.py）を使う
        PAYMENT_SERVICE=None,
        UPLOAD_FOLDER=os.path.join(app.root_path, "static", "uploads"),
        # 写真は 5MB まで。フォームの他の項目の分を足して、リクエスト全体は 6MB まで
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    )
    if config is not None:
        app.config.update(config)
    if app.config["SECRET_KEY"] == "dev" and not (app.debug or app.testing):
        # 本番で SECRET_KEY を設定し忘れると、セッションを偽造される。起動を止めて気づかせる
        raise RuntimeError(
            "SECRET_KEY を .env（本番は EnvironmentFile）に設定してください"
        )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "ログインしてください"

    from marche.services import payment

    # 外部サービスは extensions に入れる。views は current_app.extensions["payment"] 経由で呼ぶ
    app.extensions["payment"] = app.config["PAYMENT_SERVICE"] or payment

    from marche.views import auth, cart, orders, producer, shop

    for module in (auth, cart, orders, producer, shop):
        app.register_blueprint(module.bp)

    from marche import commands

    commands.init_app(app)

    @app.template_filter("yen")
    def yen(value: int) -> str:
        return f"{value:,}円"

    @app.template_filter("datetime_jp")
    def datetime_jp(value: datetime) -> str:
        """日時を「11月14日(土) 10:00」の形にする"""
        return (
            f"{value.month}月{value.day}日({WEEKDAYS[value.weekday()]}) {value:%H:%M}"
        )

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(413)
    def too_large(error):
        return render_template("errors/413.html"), 413

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    logging.basicConfig(level=logging.INFO)
    return app
