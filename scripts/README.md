# scripts

基盤の参考実装に付属する初期設定・デプロイ・検証用スクリプトです。リポジトリルートから実行します。接続先は AWS_PROFILE / AWS_REGION、環境は dev / prod で指定します。以下は構成例の手順であり、特定の環境の運用記録ではありません。認証と回復の設計要件は [ADR-001](../Documents/ADR/ADR-001_認証と許可.md) を参照してください。

## 一覧

| ファイル | 用途 |
| --- | --- |
| init-secrets.sh | origin-verify の共有秘密を SSM SecureString に登録 |
| register-slack.sh | Slack の Bot token / Webhook URL を非エコー入力から登録 |
| deploy-all.sh | shared → core → apps → edge → frontend の順で配備 |
| deploy-edge.sh | SSM から設定と秘密を読み、edge を配備 |
| deploy-frontend.sh | SPA をビルドし、S3 同期と CloudFront の無効化を実施 |
| add-admin.sh | 登録した利用者に admin グループを付与する参考ツール |
| list-mfa-status.sh | Cognito の MFA 設定を表示 |
| get_id_token.py | 対話入力を用いて検証用 ID トークンを取得 |
| validate-templates.sh | 残るスタックを sam validate / cfn-lint で検査 |

samconfig の default は dev、production は prod に対応します。PoC のスタックは一括デプロイに含みません。

## 初期設定と配備

1. AWS 接続先、ドメイン、証明書、SES の送信設定と宛先を用意します。匿名化された設定値を自分の検証環境に合わせて設定します。
2. `scripts/init-secrets.sh dev` で共有秘密を登録します。`DRY_RUN=1` では AWS を呼ばずに手順を表示します。
3. Slack を使う場合は `scripts/register-slack.sh dev bot` または `webhook` で秘密を登録します。投稿方式の設定は shared スタックが所有するため、shared/samconfig.toml の SlackMode で管理します。
4. `scripts/deploy-all.sh dev` で依存順に配備します。SES Identity の DNS 検証を完了してから DEVELOPER 送信を使います。メールコード認証の構成では SES 送信を前提とします。
5. Ops トピックの購読を確認し、通知試験を完了します。
6. 管理者の作成・有効化では、ADR D9 の TOTP 登録・別経路の本人確認・セッション確認を完了条件にします。紛失時の回復は ADR D8 の削除・再招待・再登録に従います。

## 通知の確認

core が公開する `/service-platform/{env}/ops/topic-arn` を読み、SNS のメール購読を確認します。確認待ちの場合はメールのリンクから承認し、SubscriptionArn が確定したことを確認します。購読を作る場合は `aws sns subscribe` を使います。

```bash
TOPIC=$(aws ssm get-parameter --name /service-platform/dev/ops/topic-arn \
  --query Parameter.Value --output text)
aws sns list-subscriptions-by-topic --topic-arn "$TOPIC" \
  --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output text
aws cloudwatch set-alarm-state --alarm-name service-platform-notify-dlq-dev \
  --state-value ALARM --state-reason smoke
```

通知先への到達まで確認し、試験結果を記録します。Slack の方式を変更するときは、必要な秘密を登録し、設定と実際の投稿方式が一致することを試験します。

## 秘密の扱い

秘密をコマンドライン引数、履歴、ログ、Git に保存しません。Slack の秘密はスクリプトの非エコー入力を使います。edge の配備では権限を制限した一時設定ファイルを使い、出力の秘密をマスクします。共有秘密の更新には新旧値の移行期間と、CloudFront・API 双方の疎通確認を設計します。

ID トークン取得用ツールの出力も秘密として扱います。検証後は利用者と一時生成物を削除してください。
