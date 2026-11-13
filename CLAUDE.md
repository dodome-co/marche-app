# はちまん産直マルシェ（marche-app）

八幡平の生産者が農産物・加工品を出品し、購入者がカートに入れて Stripe で支払うアプリ。拡張モジュール（任意）で、生産者が AI に商品紹介文を下書きさせる機能などを足せる。

このリポジトリは、キャンプのスターターキット（会員登録・ログイン・ロール・テストの設定・CI）から始めている。

## 技術

- Python 3.14、uv、Flask 3.1、Flask-SQLAlchemy 3.1（SQLAlchemy 2 の `Mapped` / `mapped_column`）、Flask-Migrate、Flask-Login、Flask-WTF、Jinja2、Pico CSS（CDN）
- データベースは、開発とテストは SQLite（`instance/`。テストはメモリ上）、本番は MySQL（`DATABASE_URL=mysql+pymysql://...?charset=utf8mb4`、ドライバーは PyMySQL）
- テストは pytest、整形と検査は Ruff

## 構成

- `marche/__init__.py` の `create_app(config=None)` がアプリを作る。拡張は `marche/extensions.py`（db, migrate, login_manager, csrf）
- 画面は Blueprint ごとに `marche/views/` に分ける（auth, shop, cart, orders, producer, admin, webhooks）。テンプレートは `marche/templates/<blueprint名>/`
- モデルは `marche/models.py`、フォームは `marche/forms.py`（Flask-WTF）
- 外部サービスに触るコードは `marche/services/` に閉じ込める（payment.py = Stripe、mail.py = メール。AI 機能を足すなら ai.py = Claude API）。views からは services の関数だけを呼ぶ
- 外部サービスは `create_app()` が `app.extensions["payment"]`（AI 機能を足すなら `app.extensions["ai"]` も）に入れる。views は `current_app.extensions["payment"]` 経由で呼ぶ
- テストは `tests/`。共通の fixture は `tests/conftest.py`（app、client、ユーザー、ログイン。フェイク決済は決済の章で、フェイク AI は AI の拡張で足す）。フェイクは `create_app({"PAYMENT_SERVICE": FakePayment()})` のように設定で渡す

## このプロジェクトの決まり

- 文言は日本語。日付は「11月14日(土) 10:00」、金額は「1,200円」（3桁ごとのカンマ＋円）の形式
- 認可: ログインが必要な画面は `@login_required`。ロールは `marche/decorators.py` の `@role_required(Role.PRODUCER)` で確かめる。**他人のデータ（商品、注文）は、取得したあとに所有者を必ず確かめ、違えば 403 にする**（非公開の商品の詳細は 404）
- フォームは Flask-WTF で作り、CSRF トークンを付ける。Webhook の受け口だけは CSRF の対象外にする
- 金額と在庫は**サーバー側でデータベースから計算する**。フォームから来た金額は使わない
- テンプレートで `|safe` と `Markup()` を使わない。AI が作った文章（AI 機能を足した場合）も自動エスケープのまま表示する
- SQL を文字列で組み立てない。SQLAlchemy の `select()` を使う
- SQLite でしか動かない書き方（SQLite 専用の SQL や関数。`strftime`、`PRAGMA` など）を使わない。本番の MySQL でも動く SQLAlchemy の書き方にする（文字列のカラムは `String(50)` のように長さを付ける）
- アップロードされた写真は、拡張子と中身（JPEG/PNG/WebP）とサイズ（5MB 以下）を確かめ、ファイル名はサーバーで付け直す
- （AI 機能を足した場合）Claude API は `marche/services/ai.py` の関数からだけ呼ぶ。モデルは環境変数 `ANTHROPIC_MODEL`（既定 `claude-haiku-5-5`）。失敗は `AIError` にして画面に短く出す。テストでは必ずフェイクに差し替え、本物の API を呼ばない
- テーブルを変えたら `uv run flask db migrate -m "..."` → `uv run flask db upgrade`。マイグレーションのファイルは読んでから適用する
- 機能を追加したら `tests/` に pytest のテストを追加し（正常系・権限なし・不正な入力）、`/check` が通ることを確認する

## コマンド

- コマンドは必ず `uv run` を付ける（`uv run flask run --debug`、`uv run pytest -q`、`uv run ruff check .`）。`pip` と `python` を直接使わない
- 開発サーバーはポート 8000（`.flaskenv` の `FLASK_RUN_PORT=8000`）。`migrations/` は Ruff の対象外（`pyproject.toml` の `[tool.ruff] extend-exclude`）
- パッケージの追加は `uv add <名前>`（開発用は `uv add --dev <名前>`）。追加する前に、パッケージ名と目的を説明する

## 作業の進め方

- 変更の前に、何をどのファイルに書くかを短く説明してから実装する
- 1回の指示で扱う範囲は1機能まで。大きい依頼は分割案を先に出す
- 既存のファイルの書き方に合わせる（同じフォルダーのファイルを先に読む）
- `.env` を読まない。設定の項目は `.env.example` で確かめる。API キーやパスワードをコード、ログ、コミットに含めない
- 失敗したコマンドは、原因の推測と対処案を示してから再実行する

## 設計（Day4 以降に追記していく）

- 仕様: `docs/spec/`。ユーザーストーリー: `docs/design/user-stories.md`、画面一覧: `docs/design/screens.md`、データベース設計: `docs/design/database.md`
- 用語: 生産者（producer。商品を出品する）、購入者（customer）、管理者（admin。`flask create-admin` でだけ作る）、商品（products）、カート（セッションの `{"商品ID": 数量}`。テーブルにしない）、注文（orders。pending → paid / cancelled）、注文明細（order_items。注文時点の商品名と単価を持つ）

### URL と認可

- URL の接頭辞で対象を分ける。`/producer/` は生産者、`/orders/` はログインユーザー。値を変える操作（削除、公開の切り替え、カート、注文の作成・取り消し）は全て POST
- 商品の編集・削除・公開の切り替えは `producer_id` が自分か確かめ、違えば 403。非公開の商品の詳細は、出品者と管理者以外には 404
- 注文の閲覧・完了・中断は、注文した本人（`user_id`）と管理者だけ。他人は 403

### データ

- 出品者は `products.producer_id`、注文者は `orders.user_id`（どちらも users.id）。`role` と `status` は文字列で保存し、Python では StrEnum（`Role`、`OrderStatus`）で扱う
- 金額は円の整数。注文の金額はサーバーが DB の価格で計算し、明細に注文時点の商品名と単価を保存する
- 注文がある商品は削除させず、非公開にしてもらう。SQLite では外部キー制約が効かないので、ビューで先に確かめる
- 在庫の `stock` は「いまある数」で、支払いが確定したときに減らす。表示と注文の作成では `marche/services/inventory.py` の `available_stock()`（stock − 35 分以内の支払い待ち）を使う
- `paid` への更新は `status = pending` のときだけの条件付き更新にする（在庫を二重に減らさない）
- 1人が同時に持てる支払い待ち（35 分以内）の注文は `MAX_PENDING_ORDERS`（1件）まで。払わない注文を繰り返して、在庫を押さえ続けられないようにする（`create_order` が確かめる）

### 外部サービス

- Stripe を呼ぶコードは `marche/services/payment.py` だけに書く（AI 機能を足すなら、Claude API は `marche/services/ai.py` だけ）
- テストでは本物の API を呼ばず、`create_app({"PAYMENT_SERVICE": FakePayment()})` のように設定でフェイクに差し替える
- Webhook（`POST /stripe/webhook`）だけ CSRF の対象外にし、Stripe の署名で確かめる
- （AI 機能を足した場合）AI の出力はそのまま保存・公開しない。下書きとしてフォームに入れ、人が確認して「保存」を押したときだけ保存する
