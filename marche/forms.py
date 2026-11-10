from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import (
    BooleanField,
    EmailField,
    IntegerField,
    PasswordField,
    RadioField,
    StringField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Regexp,
    ValidationError,
)

from marche.models import Role
from marche.uploads import detect_media_type

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 写真は 5MB まで

# email-validator パッケージを足さずに済むよう、形だけを正規表現で確かめる
EMAIL = Regexp(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="メールアドレスの形式で入力してください"
)


class RegisterForm(FlaskForm):
    name = StringField("名前", validators=[DataRequired(), Length(max=50)])
    email = EmailField(
        "メールアドレス", validators=[DataRequired(), EMAIL, Length(max=255)]
    )
    password = PasswordField("パスワード", validators=[DataRequired(), Length(min=8)])
    # 選択肢に admin を入れない。送られてきても RadioField の検証で弾かれる
    role = RadioField(
        "登録の種類",
        choices=[(Role.CUSTOMER, "購入者"), (Role.PRODUCER, "生産者")],
        default=Role.CUSTOMER,
    )


class LoginForm(FlaskForm):
    email = EmailField("メールアドレス", validators=[DataRequired(), EMAIL])
    password = PasswordField("パスワード", validators=[DataRequired()])


class WholeNumberField(IntegerField):
    """整数の入力欄。数字でない入力のメッセージを日本語にする"""

    def process_formdata(self, valuelist):
        try:
            super().process_formdata(valuelist)
        except ValueError:
            self.data = None
            raise ValueError("整数で入力してください") from None


class ProductForm(FlaskForm):
    # producer_id は含めない。出品者は、ログイン中のユーザーからビューで決める
    name = StringField(
        "商品名",
        validators=[
            DataRequired(message="商品名を入力してください"),
            Length(max=50, message="商品名は50文字以内で入力してください"),
        ],
    )
    price = WholeNumberField(
        "価格（円）",
        validators=[
            InputRequired(message="価格を入力してください"),
            NumberRange(
                min=50, max=100_000, message="価格は50円から100,000円で入力してください"
            ),
        ],
    )
    stock = WholeNumberField(
        "在庫数",
        validators=[
            InputRequired(message="在庫数を入力してください"),
            NumberRange(min=0, max=999, message="在庫数は0から999で入力してください"),
        ],
    )
    description = TextAreaField(
        "紹介文",
        validators=[
            Length(max=2000, message="紹介文は2,000文字以内で入力してください")
        ],
    )
    is_published = BooleanField("公開する")
    # 写真は任意。選ばなければ、検証も保存もしない
    image = FileField(
        "写真",
        validators=[
            FileAllowed(
                ["jpg", "jpeg", "png", "webp"],
                message="写真は JPEG・PNG・WebP のファイルを選んでください",
            ),
            FileSize(max_size=MAX_IMAGE_SIZE, message="写真は5MB以下にしてください"),
        ],
    )

    def validate_image(self, field):
        # 拡張子だけでは偽装できるので、ファイルの中身（先頭のバイト）も確かめる
        if not field.data:
            return
        data = field.data.read()
        field.data.seek(0)
        if detect_media_type(data) is None:
            raise ValidationError("写真は JPEG・PNG・WebP の画像を選んでください")


class CartForm(FlaskForm):
    # 数量だけを受け取る。金額は受け取らない（単価は DB の商品から取る）。
    # 範囲（1〜min(10, いま買える数)）は、いま買える数が要るのでビューで確かめる
    quantity = WholeNumberField(
        "数量", validators=[InputRequired(message="数量を入力してください")]
    )
