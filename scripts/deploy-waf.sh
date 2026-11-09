#!/usr/bin/env bash
# CloudFront 用 WAF と SSM を us-east-1 に配備する（設計 13.3）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AWS_PAGER=""

usage() {
  echo "usage: scripts/deploy-waf.sh {dev|prod}" >&2
}

if [ "$#" -ne 1 ]; then
  usage
  exit 1
fi

ENV_NAME="$1"
case "${ENV_NAME}" in
  dev) CFG_ENV="default" ;;
  prod) CFG_ENV="production" ;;
  *) usage; exit 1 ;;
esac

echo "=== CloudFront WAF デプロイ (${ENV_NAME}, us-east-1) ==="
# Lambda コードを持たないので sam build は不要。
cd "${ROOT}/infra/waf"
sam deploy --region us-east-1 --config-env "${CFG_ENV}" --no-fail-on-empty-changeset
