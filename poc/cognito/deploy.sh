#!/usr/bin/env bash
# PoC スタックを作る / 更新する。CHANGE_PASSWORD_RULE=Block で ChangePassword を WAF で止める (PoC 14)
set -euo pipefail
cd "$(dirname "$0")"
STACK="${STACK:-service-platform-poc-cognito-dev}"
REGION="${AWS_REGION:-ap-northeast-1}"
RP_ID="${RP_ID:-localhost}"
SES_DOMAIN="${SES_DOMAIN:-ses-sender-dev.example-cloud.com}"
RULE="${CHANGE_PASSWORD_RULE:-Count}"

aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK" \
  --template-file template.yaml \
  --no-fail-on-empty-changeset \
  --parameter-overrides "RpId=$RP_ID" "SesDomain=$SES_DOMAIN" "ChangePasswordRuleAction=$RULE" \
  --tags Project=service-platform Name=PoC

aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query 'Stacks[0].Outputs' --output json > web/site/config.raw.json
echo "outputs -> web/site/config.raw.json"
