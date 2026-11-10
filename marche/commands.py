import click

from marche.extensions import db
from marche.models import Order, OrderItem, Product, Role, User
from marche.uploads import delete_image

SEED_PASSWORD = "password"

# (ロール, 名前, メールアドレス)
SEED_USERS = [
    (Role.ADMIN, "管理者", "admin@example.com"),
    (Role.PRODUCER, "高原ファーム", "producer1@example.com"),
    (Role.PRODUCER, "岩手山麓工房", "producer2@example.com"),
    (Role.CUSTOMER, "購入者1", "customer1@example.com"),
    (Role.CUSTOMER, "購入者2", "customer2@example.com"),
]

# (生産者のメールアドレス, 商品名, 価格, 在庫, 公開するか)
SEED_PRODUCTS = [
    ("producer1@example.com", "八幡平マッシュルーム 200g", 450, 20, True),
    ("producer1@example.com", "安代りんどう 切り花 5本", 800, 10, True),
    ("producer1@example.com", "ほうれん草 1束", 180, 30, True),
    ("producer1@example.com", "試作中の干し野菜セット", 600, 5, False),
    ("producer2@example.com", "八幡平ポーク ロース 300g", 1200, 8, True),
    ("producer2@example.com", "手づくり味噌 1kg", 1300, 12, True),
    ("producer2@example.com", "りんごジュース 1L", 700, 15, True),
    ("producer2@example.com", "地熱野菜のピクルス", 550, 0, True),  # 売り切れの例
]


def is_sqlite() -> bool:
    """開発・テストの SQLite か。本番は MySQL なので、False になる"""
    return db.engine.url.get_backend_name() == "sqlite"


def init_app(app):
    @app.cli.command("seed")
    @click.option("--yes", is_flag=True, help="確認せずに実行する")
    def seed(yes):
        """全てのデータを消して、初期データを入れ直す（開発用。SQLite のときだけ実行できる）"""
        # 本番（MySQL）の注文などを消さないよう、確認よりも先に止める。--yes でも止まる
        if not is_sqlite():
            raise click.ClickException(
                "seed は開発用です。SQLite 以外のデータベースでは実行できません"
            )
        if not yes:
            click.confirm(
                "全てのデータを消して、初期データを入れ直します。よろしいですか？",
                abort=True,
            )
        # 消した商品の写真のファイルも消せるように、先に名前を控えておく
        filenames = db.session.scalars(
            db.select(Product.image_filename).where(Product.image_filename.is_not(None))
        ).all()
        try:
            # 外部キーの参照される側があとになるように、明細 → 注文 → 商品 → ユーザーの順に消す
            for model in (OrderItem, Order, Product, User):
                db.session.execute(db.delete(model))
            users = {}
            for role, name, email in SEED_USERS:
                user = User(name=name, email=email, role=role)
                user.set_password(SEED_PASSWORD)
                db.session.add(user)
                users[email] = user
            db.session.flush()  # ユーザーの id を決める
            for email, name, price, stock, is_published in SEED_PRODUCTS:
                db.session.add(
                    Product(
                        producer_id=users[email].id,
                        name=name,
                        price=price,
                        stock=stock,
                        is_published=is_published,
                    )
                )
            # 削除と投入を1回のコミットにまとめる。途中で失敗したら元のデータに戻る
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        # 写真のファイルは、コミットが成功したあとで消す
        for filename in filenames:
            delete_image(filename)
        click.echo(
            f"初期データを入れました（ユーザー {len(SEED_USERS)}、"
            f"商品 {len(SEED_PRODUCTS)}）。パスワードは全員 {SEED_PASSWORD}"
        )

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
