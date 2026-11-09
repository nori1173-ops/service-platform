#!/usr/bin/env bash
# 全ての admin が TOTP を失くしたときの緊急の復旧（ADR-001 D9 の手順 2）。中身は scripts/admin_ops.py emergency-reinvite。
#
# 使い方:
#   scripts/emergency-reinvite-admin.sh dev someone@example.co.jp --reason "TOTP の端末を紛失" [--output-dir DIR]
#
# 先に社内の別経路で本人であることを確かめる。台帳で停止 → 無効化 → 全セッションの失効 → 削除 →
# 一時パスワードで作り直し (招待メールは送らない) → 古い sub に無効の印 → admin グループ → 準備中、の順に行う。
# 本人が TOTP を登録し直した後に scripts/admin_ops.py activate-admin で有効に戻す。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$#" -lt 2 ]; then
  echo "usage: scripts/emergency-reinvite-admin.sh {dev|prod} {email} --reason REASON [--output-dir DIR]" >&2
  exit 1
fi

cd "${ROOT}"
if command -v uv >/dev/null 2>&1; then
  exec uv run python scripts/admin_ops.py emergency-reinvite "$@"
fi
exec python3 scripts/admin_ops.py emergency-reinvite "$@"
