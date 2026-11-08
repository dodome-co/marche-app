import click

from marche.extensions import db
from marche.models import Role, User


def init_app(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt="メールアドレス")
    @click.option("--name", prompt="名前", default="管理者")
    @click.password_option("--password", prompt="パスワード")
    def create_admin(email, name, password):
        """管理者を1人作る（画面からは作れないため）"""
        email = email.strip().lower()
        if db.session.scalar(db.select(User).filter_by(email=email)):
            raise click.ClickException("このメールアドレスは登録済みです")
        if len(password) < 8:
            raise click.ClickException("パスワードは8文字以上にしてください")
        user = User(name=name, email=email, role=Role.ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"管理者 {email} を作りました")
