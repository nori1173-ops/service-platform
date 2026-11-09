#!/usr/bin/env bash
# 最初の admin を作る（ADR-001 D9、設計 13.4 の手順 11）。中身は scripts/admin_ops.py create-admin。
#
# 使い方:
#   scripts/add-admin.sh dev someone@example.co.jp [--output-dir DIR]
#   scripts/add-admin.sh prod someone@example.co.jp
#
# Cognito に招待メールを送らずに作り (一時パスワード)、台帳の行・admin グループ (準備中)・監査を書く。
# 一時パスワードは標準出力に出さず、リポジトリの外の権限 600 のファイルに書いてパスだけを示す。
# 本人の TOTP の登録と別経路の確認の後に scripts/admin_ops.py activate-admin で有効にする。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$#" -lt 2 ]; then
  echo "usage: scripts/add-admin.sh {dev|prod} {email} [--output-dir DIR]" >&2
  exit 1
fi

cd "${ROOT}"
if command -v uv >/dev/null 2>&1; then
  exec uv run python scripts/admin_ops.py create-admin "$@"
fi
exec python3 scripts/admin_ops.py create-admin "$@"
