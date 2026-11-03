#!/usr/bin/env bash
# PoC スタックを消し、消えたことを確かめる
set -euo pipefail
cd "$(dirname "$0")"
STACK="${STACK:-service-platform-poc-cognito-dev}"
REGION="${AWS_REGION:-ap-northeast-1}"

aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK"
aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK"
if aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" >/dev/null 2>&1; then
  echo "stack still exists: $STACK" >&2
  exit 1
fi
echo "deleted: $STACK"
rm -f web/site/config.raw.json web/site/config.json
# out/ には PoC の利用者のトークンと仮想認証器の鍵がある
rm -f out/*.json
rmdir out 2>/dev/null || true
