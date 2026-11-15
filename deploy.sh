#!/usr/bin/env bash
# 本番サーバーでの更新手順。途中で失敗したら、そこで止まる。
set -euo pipefail

# SSH から呼ばれたときに uv が見つかるようにする
export PATH="$HOME/.local/bin:$PATH"

echo "==> アプリのフォルダーに移動します"
cd /var/www/marche-app

echo "==> 最新のコードを取得します (git pull --ff-only)"
git pull --ff-only origin main

echo "==> 依存パッケージを同期します (uv sync --frozen --no-dev)"
uv sync --frozen --no-dev

echo "==> データベースを更新します (flask db upgrade)"
uv run --frozen --no-dev flask db upgrade

echo "==> アプリを再起動します (systemctl restart)"
sudo -n systemctl restart marche-app

echo "==> 起動を確かめます (2 秒待ってから is-active)"
sleep 2
systemctl is-active marche-app

echo "==> デプロイが完了しました"
