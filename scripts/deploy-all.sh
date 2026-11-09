#!/usr/bin/env bash
# 全スタックを依存順にデプロイする（DESIGN 3.3 / 13.3。初回の SES 検証・Ops 購読・admin 作成は RUNBOOK の段階手順）。
#
# 使い方:
#   scripts/deploy-all.sh dev
#   scripts/deploy-all.sh prod
#
# 順序: shared -> core -> waf -> apps/* -> edge -> frontend
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export AWS_PAGER=""

usage() {
  cat >&2 <<'USAGE'
usage: scripts/deploy-all.sh {dev|prod}

前提:
  scripts/init-secrets.sh {env} で origin-verify 秘密が登録済みであること。
  Slack を使う場合は scripts/register-slack.sh {env} {bot|webhook} も先に実行する。

環境変数:
  AWS_PROFILE / AWS_REGION  AWS CLI の接続先
USAGE
}

if [ "$#" -ne 1 ]; then
  usage
  exit 1
fi

ENV_NAME="$1"
case "${ENV_NAME}" in
  dev)
    CFG_ENV="default"
    ;;
  prod)
    CFG_ENV="production"
    ;;
  *)
    usage
    exit 1
    ;;
esac

deploy_stack() {
  local label="$1" dir="$2"
  echo ""
  echo "=============================================="
  echo "  ${label}  (${dir})"
  echo "=============================================="
  (
    cd "${dir}"
    sam build --use-container
    sam deploy --config-env "${CFG_ENV}" --no-fail-on-empty-changeset
  )
}

echo "=== service-platform 一括デプロイ ==="
echo "  Env     : ${ENV_NAME} (--config-env ${CFG_ENV})"
echo "  Region  : ${REGION}"
echo "  Profile : ${AWS_PROFILE:-(未設定。既定プロファイルを使う)}"

deploy_stack "shared" "${ROOT}/shared"
deploy_stack "core" "${ROOT}/infra/core"
"${ROOT}/scripts/deploy-waf.sh" "${ENV_NAME}"

shopt -s nullglob
for app_dir in "${ROOT}"/apps/*/; do
  app="$(basename "${app_dir%/}")"
  if [ -f "${app_dir}template.yaml" ]; then
    deploy_stack "app: ${app}" "${app_dir%/}"
  fi
done
shopt -u nullglob

echo ""
echo "=============================================="
echo "  edge"
echo "=============================================="
"${ROOT}/scripts/deploy-edge.sh" "${ENV_NAME}"

echo ""
echo "=============================================="
echo "  frontend"
echo "=============================================="
"${ROOT}/scripts/deploy-frontend.sh" "${ENV_NAME}"

echo ""
echo "=============================================="
echo "  camera の SPA (/camera/)"
echo "=============================================="

echo ""
echo "一括デプロイ完了 (${ENV_NAME})"
