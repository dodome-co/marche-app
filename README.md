# はちまん産直マルシェ（marche-app）

八幡平市スパルタキャンプ 2026年11月 Python編の本命アプリです。  
このリポジトリは**スターターキット**です。会員登録・ログイン・ロール、テストの設定、CI、Claude Code の設定が最初から入っています。商品、カート、注文、決済は、Day4 から Claude Code と一緒に作ります（AI 紹介文などの AI 機能は、拡張モジュールで任意に足します）。

## 動かし方

```bash
uv sync
cp .env.example .env
uv run flask db upgrade
uv run flask run
```

ブラウザで http://127.0.0.1:8000 を開きます。

## 入っているもの

| 場所 | 中身 |
|-|-|
| `marche/__init__.py` | アプリを作る `create_app()` |
| `marche/extensions.py` | データベース、マイグレーション、ログイン、CSRF の拡張 |
| `marche/models.py` | `User` とロール（購入者・生産者・管理者） |
| `marche/forms.py`、`marche/views/auth.py` | 会員登録、ログイン、ログアウト |
| `marche/views/shop.py` | トップページ（商品の一覧は Day4 で作る） |
| `marche/commands.py` | 管理者を作るコマンド `uv run flask create-admin` |
| `marche/templates/` | 共通レイアウト（Pico CSS）、ログイン・会員登録の画面、403・404 の画面 |
| `migrations/` | `users` テーブルを作るマイグレーション |
| `tests/` | テストの設定（`conftest.py`）、会員登録・ログインのテスト |
| `.github/workflows/ci.yml` | GitHub Actions の CI（Ruff と pytest） |
| `.claude/` | Claude Code の権限設定、サブエージェント（reviewer、security-audit、test-writer）、`/check` |
| `CLAUDE.md` | Claude Code に伝えるプロジェクトの決まり。Day4 の設計で「## 設計」を書き足す |
| `docs/spec/` | 仕様書 |

## よく使うコマンド

| やりたいこと | コマンド |
|-|-|
| 開発サーバーを起動 | `uv run flask run` |
| テスト | `uv run pytest -q` |
| 整形と検査 | `uv run ruff format .`、`uv run ruff check .` |
| テーブルの変更を反映 | `uv run flask db migrate -m "説明"` → `uv run flask db upgrade` |
| 管理者を作る | `uv run flask create-admin` |
