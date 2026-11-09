# scripts

service-platform の秘密登録・デプロイ・運用スクリプト。シェルと Python の入口があり、
リポジトリのルートから実行する。AWS の接続先は環境変数（`AWS_PROFILE` / `AWS_REGION`）で
指定する（`samconfig.toml` にプロファイルは書かない）。

- `{env}` は `dev` または `prod`。それ以外を渡すと usage を表示して終了コード 1。
- `sam deploy` の `--config-env` は `dev` → `default`、`prod` → `production` に対応する。
- 秘密（origin-verify・Slack bot token・Webhook URL・パスワード・ID トークン）は
  引数・標準出力・ログに出さない。`jq` は使わない（AWS CLI の `--query` と `python3`）。

## 初回セットアップ

[RUNBOOK](../Documents/RUNBOOK.md) の「作業前の確認」から順に進める。秘密と shared → core の初回構築 → SES の検証と送信方式の切り替え → Ops トピックの購読と通知試験 → waf・apps・edge・frontend → 最初の admin の登録時刻の確認・有効化までを行う。

初回は SES の検証待ちと手動の購読承認があるため、一括配備から始めない。配備対象と変更セットを作業前に確認する。

`sam deploy --parameter-overrides` は samconfig の `parameter_overrides` にある一覧を丸ごと置き換える。同じ名前の CloudFormation パラメータだけを部分的に置き換える動作ではない。送信方式だけを渡すと他の設定が失われるため、RUNBOOK の「初回の構築：core と SES の切り替え」にある、一覧を読み取って送信方式を差し替える例を使う。

Ops トピックの購読承認と DLQ アラームの通知試験も RUNBOOK を参照する。Slack の投稿方式は shared/samconfig.toml の SlackMode で管理する。`/shared/slack/mode` は CloudFormation 管理なのでスクリプトから先に作らない。

## 一覧

| スクリプト | 引数 | 用途 |
| --- | --- | --- |
| `init-secrets.sh` | `{env}` | `/service-platform/{env}/edge/origin-verify-secret` を SecureString で登録する |
| `register-slack.sh` | `{env} {bot\|webhook}` | Slack の Bot token / Webhook URL を登録する（`/shared/slack/mode` は書かない） |
| `deploy-waf.sh` | `{env}` | CloudFront 用 WAF と SSM を us-east-1 に配備する |
| `deploy-edge.sh` | `{env}` | SSM の秘密・API ドメインと、us-east-1 の web ACL の ARN を取り出して edge を配備する。WAF のキーが未登録なら dev は ACL なし、prod と取得の障害は停止 |
| `deploy-frontend.sh` | `{env}` | frontend をビルドして S3 へ同期しCloudFront を無効化する |
| `deploy-all.sh` | `{env}` | shared → core → waf → apps/\* → edge → frontend の順に一括デプロイする |
| `add-admin.sh` | `{env} {email} [--output-dir DIR]` | 最初の admin を作る（`admin_ops.py create-admin` の入口） |
| `emergency-reinvite-admin.sh` | `{env} {email} --reason REASON [--output-dir DIR]` | 全ての admin が TOTP を失くしたときの緊急の復旧（`admin_ops.py emergency-reinvite` の入口） |
| `admin_ops.py` | `create-admin` / `show-admin` / `activate-admin` / `enable-passkey-mfa` / `emergency-reinvite` / `check-waf` | admin の作成・有効化・パスキー MFA の有効化・緊急の復旧と、ユーザープールの web ACL の確認（下記） |
| `list-mfa-status.sh` | `{env}` | Cognito ユーザーの MFA 設定状況を一覧する |
| `get_id_token.py` | `{env} {email} [--password-file PATH] [--new-password-file PATH] [--totp-code CODE]` | USER_AUTH (PASSWORD) でサインインし、Cognito の ID トークンを標準出力へ出す（一時パスワード → 本パスワード、TOTP の問いに答える） |
| `e2e-users.py` | `create dev --secrets-dir DIR` / `delete dev --secrets-dir DIR` | dev 専用の E2E 利用者をメール送信なしで作成・削除する。秘密の置き場は Linux 側のリポジトリ外に必ず指定する ([RUNBOOK](../Documents/RUNBOOK.md#dev-の-e2e-と片付け)) |
| `validate-templates.sh` | なし | 全テンプレートに `sam validate` と `cfn-lint` をかける |

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

値は**引数では渡せない**（シェル履歴に秘密を残さないため）。実行後のプロンプトから
非エコーで入力する。引数が 2 個でないときは usage を出して終了コード 1。
`init-secrets.sh` と同様に `DRY_RUN=1` で AWS を呼ばずに実行内容だけ表示できる。

登録するパラメータ:

| 種別 | パラメータ | 型 |
| --- | --- | --- |
| `bot` | `/service-platform/{env}/shared/slack/bot-token` | SecureString |
| `webhook` | `/service-platform/{env}/shared/slack/webhook-url` | SecureString |

Bot token に要るスコープは `chat:write` + `chat:write.public`（投稿）。
`bot-token` を読むのは notifier（投稿）だけである。

どちらも秘密の登録・ローテートだけを行い、投稿方式（`/shared/slack/mode`）は書かない。
このパラメータは shared スタックが `SlackMode` から作る CloudFormation 管理のリソースで、
スクリプトが先に作ると shared の作成が `AlreadyExists` で失敗する。
方式を切り替えるときは `shared/samconfig.toml` の `SlackMode` を直して shared を再デプロイする。
samconfig はスクリプトからは編集しない。

notifier の実挙動は bot-token の有無で決まるため、`mode` は「人が読むための表示値」に過ぎない。
`SlackMode=bot` にしたまま bot-token を消すと、画面は bot と表示しつつ実際は webhook で投稿される。

## deploy-edge.sh

prod は WAF の ARN が未登録なら停止する。明示的に外す場合だけ `ALLOW_NO_WAF=1 scripts/deploy-edge.sh prod` を使う（dev は未登録でも ACL なしで配備する）。

```bash
scripts/deploy-edge.sh dev
```

1. `/service-platform/{env}/edge/origin-verify-secret` を `--with-decryption` で取得する
   （未登録なら「先に init-secrets.sh を実行」と出して終了コード 1）。
2. `/service-platform/{env}/apps/admin-users/api-domain` があれば
   `AdminUsersApiDomain` に足す。無ければ足さない（API 経路なしで edge が立つ）。base も同じ。
3. `infra/edge/samconfig.toml` の `parameter_overrides` を `tomllib` で読み、上の値を
   連結してパーミッション 600 の一時 samconfig（`/tmp`）へ書き、`sam deploy --config-file`
   で渡す。一時ファイルは `trap` で必ず消す。
4. `sam deploy` の出力は秘密を `***` に置換してから表示する。

`sam deploy --parameter-overrides` は **samconfig の parameter_overrides 一覧を丸ごと置き換える**
（追記ではない）ため、`SiteDomain` / `HostedZoneId` / `AcmCertificateArn` を
消さないよう samconfig 側の値を前置している。
edge テンプレートは Lambda コードを持たない（CloudFront Function はインライン）ので
`sam build` は行わない。

> **注意（秘密の露出範囲）**: `sam deploy` は `Deploying with following values` の banner に
> `parameter_overrides` をそのまま印字する（`sam deploy` 側の挙動で抑止オプションが無い。
> CloudFormation コンソール上は `NoEcho` でマスクされる）。このスクリプトは
> (1) 秘密をコマンドライン引数ではなく 600 の一時 samconfig 経由で渡し、
> (2) `sam` の出力から秘密を `***` に置換することで、`ps` と端末・ログの両方から隠している。
> それでも **prod のデプロイログを不用意に保存・共有しないこと。** 漏れた疑いがあるときは
> `ROTATE=1 scripts/init-secrets.sh {env}` でローテーションしてから `deploy-edge.sh` を流し直す。

## deploy-frontend.sh

```bash
scripts/deploy-frontend.sh dev
```

SSM から `/cognito/user-pool-id` と `/cognito/client-id`、edge スタックの Outputs から
`SpaBucketName` / `DistributionId` / `SiteUrl` を取得し、次を実行する。

```
npm ci
VITE_COGNITO_USER_POOL_ID=... VITE_COGNITO_CLIENT_ID=... VITE_SITE_ENV={env} npm run build
aws s3 sync dist/ s3://{bucket}/ --delete
aws s3 cp dist/index.html s3://{bucket}/index.html --cache-control no-cache --content-type text/html
aws cloudfront create-invalidation --paths "/*"
```

`prod` は `--delete` 付き同期の前に確認プロンプトを出す。

`VITE_SITE_ENV`（`dev` / `prod`）はアプリバーのサイト環境チップと `document.title` の
`[検証]` 前置を決める。渡し忘れたビルドは `dev`（検証環境）として表示される。

## deploy-all.sh

```bash
scripts/deploy-all.sh dev
```

shared → core → waf → apps（template.yaml があるもの）→ edge → frontend の順に配備する。Lambda コードを持つ shared／core／apps は先に sam build を実行する。初回の SES 検証と admin の設定は RUNBOOK の段階手順で行う。

## get_id_token.py

```bash
ID_TOKEN=$(uv run python scripts/get_id_token.py dev someone@example.com)
ID_TOKEN=$(uv run python scripts/get_id_token.py dev someone@example.com --totp-code 123456)
# 一時パスワードでの初回 (NEW_PASSWORD_REQUIRED で本パスワードを決める)
ID_TOKEN=$(uv run python scripts/get_id_token.py dev someone@example.com \
  --password-file {一時パスワードのファイル} --new-password-file {本パスワードのファイル})
```

SPA 用 App Client の USER_AUTH (choice-based) で、要素に PASSWORD を選んでサインインする (ADR D1、判断 6)。
問いが来たら答える: NEW_PASSWORD_REQUIRED (本パスワードを `--new-password-file` か環境変数 `COGNITO_NEW_PASSWORD` から)、
SOFTWARE_TOKEN_MFA (`--totp-code` か対話入力)。パスワードは引数では渡せない（`--password-file`、
環境変数 `COGNITO_PASSWORD`、対話入力の順）。ファイルはリポジトリの外に権限 600 で置く。
標準出力へ出るのは ID トークンだけで、パスワード・アクセストークン・リフレッシュトークンは出さない。
パスワードを持たない利用者 (お客様) はこのスクリプトでは入れない (パスキーとメールのコードは画面で使う)。

## add-admin.sh / admin_ops.py

最初の admin と緊急の復旧は、IAM の権限を持つ運用者がスクリプトで行う（ADR-001 D9）。
2 人目からの admin の招待・有効化は admin-users の画面から行う。

```bash
# 1. 最初の admin を作る（準備中）
scripts/add-admin.sh dev {email}
# 2. 本人が一時パスワードでサインインし、本パスワードを決め、アカウントの画面で TOTP を登録する（確認待ち）
# 3. 台帳の登録時刻 (JST) と Cognito の状態を表示し、本人に別経路で登録時刻を確かめる
uv run python scripts/admin_ops.py show-admin dev {email}
# 4. 表示内容と本人の操作を突き合わせてから有効にする (y/N)
uv run python scripts/admin_ops.py activate-admin dev {email} --method phone --note "確かめたこと"
# 5. 本人がパスワード + TOTP でサインインし直すと admin の操作が通る
```

| サブコマンド | すること |
| --- | --- |
| `create-admin {env} {email}` | AdminCreateUser（一時パスワード、`email_verified=true`、招待メールは送らない `SUPPRESS`）→ admin グループ → 台帳の利用者の行（`role=admin`、`kind=employee`、`admin_state=pending`）と sub の引き当てと監査 `script.create_admin` を 1 つのトランザクションで書く。途中で失敗したら Cognito の利用者を消して戻す |
| `show-admin {env} {email}` | PROFILE の status・admin_state・mfa_enabled_at (JST)・reinvite_in_progress・last_op_id と Cognito の Enabled・UserStatus・UserMFASettingList、SUB の対応の有無を表示する。秘密や確認メモは表示しない。Cognito が無くても email で台帳を探し、PROFILE は強い整合で読み直す |
| `activate-admin {env} {email} --method {phone\|in_person\|video\|other} [--note TEXT] [--yes]` | Cognito で有効・admin グループ・TOTP (`UserMFASettingList`) を確かめ、台帳の `admin_state=awaiting` と `mfa_enabled_at` を確かめてから `active` にする。再招待の後（`status=suspended`、`restore_requires=reinvite`）なら利用者の台帳も `active` に戻す。確かめた方法を台帳と監査 `script.activate_admin` に、メモは台帳にだけ残す |
| `enable-passkey-mfa {env} {email}` | Cognito の TOTP が有効、台帳が `status=active`・`admin_state=active` で `mfa_enabled_at` があり、再招待の途中でないことと `WEB_AUTHN` が登録済みであることを確かめる → 監査 `script.enable_passkey_mfa` の `step=requested` → AdminSetUserMFAPreference で TOTP を有効・優先のままパスキー MFA を有効にする → 同じ監査の `step=enabled`。prod は実行前に確認を求める |
| `emergency-reinvite {env} {email} --reason TEXT` | 台帳で停止（監査 `script.emergency_reinvite` の `step=suspend` と同じトランザクション）→ AdminDisableUser → AdminUserGlobalSignOut → AdminDeleteUser → AdminCreateUser（一時パスワード、`SUPPRESS`）→ admin グループ → 台帳の sub の付け替え・古い sub に無効の印 (`revoked_at`)・`admin_state=pending`・`mfa_enabled_at` を消す・監査 `script.emergency_reinvite` の `step=reinvite`（どちらも理由と実行した IAM の主体）。途中で止まったら同じコマンドを流し直す（下の「途中で止まったとき」） |
| `check-waf {env}` | ユーザープールの web ACL の規則を表示し、`change-password` が Block であることと、ChangePassword の要求が WAF の 403（`Request not allowed due to WAF block.`）になることを確かめる。prod は ChangePassword を送る前に確認を求める |

- 一時パスワードは引数・標準出力・ログ・台帳・監査に出さない。リポジトリの外の権限 600 のファイルに書き、
  パスだけを表示する（既定は権限 700 の新しい一時ディレクトリ。`--output-dir` で置き場を選べるが、
  リポジトリの中と書けない場所は拒む）。置き場は Cognito や台帳を変える前に確かめ、空のファイルを先に作る。
  社内の別経路で本人に渡し、渡したらファイルを消す。
- activate-admin は登録時刻 (JST) と Cognito の TOTP の状態を表示し、本人に別経路で登録時刻を確かめたか y/N で確認する。確認済みの非対話実行には --yes を使えるが、prod は省略できない。show-admin は読み取りだけで確認不要。ほかの変更コマンドも prod は実行前に確認を求める。
- 監査の主体は `ACTOR#script`。実行した IAM の主体（STS GetCallerIdentity の ARN）を `detail` に残し、
  台帳の `created_by`・`updated_by`・`suspended_by`・`admin_verified_by` には ARN の末尾の名前を
  `script:{名前}` で残す。Cognito の管理 API の呼び出しは、アカウントの CloudTrail にも残る。
- TOTP を失くした admin の回復に「TOTP の解除だけ」は使わない（ADR 判断 13。TOTP を外すと、
  TOTP の間に登録したパスキーとメールのコードで入れる）。使える admin がいれば admin-users の再招待、
  いなければ `emergency-reinvite` を使う。先に社内の別経路で本人であることを確かめる。

途中で止まったとき（`create-admin` と `emergency-reinvite`）:

- 台帳のトランザクションには ClientRequestToken を付ける。応答を失った（タイムアウト・接続の切断）、
  処理中（`TransactionInProgressException`）、5xx、スロットリングのときは、同じ token で同じ要求を
  最大 3 回（2 秒・5 秒・10 秒の間隔）送り直す。DynamoDB は 10 分の間、同じ token の同じ要求を冪等に扱う。
- 送り直しても全部結果が分からなければ「結果が分かりません」と出して止まる。作った Cognito の利用者は
  消さず、一時パスワードのファイルも残す。成否が確定した失敗が返ったときは台帳を読み直し、反映されていれば
  成功として続け、反映されていないときだけ作った利用者を消して（補償）ファイルも消す。読み直しが失敗したときと、
  補償の削除の結果が分からない・失敗したときも、結果不明として止まりファイルを残す（後者は
  「Cognito に利用者が残っている可能性」を出す。手で確かめる）。
- `emergency-reinvite` は送りかけた台帳の要求を、一時パスワードのファイルと同じ置き場の状態のファイル
  （`emergency-reinvite-{env}-{user_id}.json`、権限 600、一時パスワードは入れない）に残す。止まったときは、
  表示された置き場を `--output-dir` に付けて同じコマンドを流し直す。
  - 前の回の台帳の書き込みが実は通っていたら、「前の回で済んでいました」と出し、何も変えずに終える
    （一時パスワードは前の回のファイル）。
  - 通っていなければ、前の回と同じ token・同じ中身で送り直す。
  - それ以外も Cognito と台帳の今の状態から続きの段を決める。Cognito に利用者がいなければ作り直しから、
    台帳に無い sub の利用者がいれば（作り直した後で台帳が未更新）一時パスワードを出し直して台帳の段から進む。
    後者は、台帳が再招待の途中（停止・`restore_requires=reinvite`）で、その利用者が `FORCE_CHANGE_PASSWORD`
    のときだけ。それ以外は止めるので手で確かめる。流し直したときの一時パスワードは新しいファイルにあり、
    状態のファイルが案内するのもその新しいファイル。
  - 台帳の書き込みが通った後に状態のファイルを書き直せない・消せないときは、警告を出して続ける
    （利用者もファイルも消さない）。警告に出た状態のファイルを手で消す。
- 緊急復旧の担当を決め、同じ対象への操作は一連の手順を完了してから次へ進む。

`check-waf` は、既定ではプールとクライアントだけを名乗る署名の無効なトークンで ChangePassword を呼ぶ。
WAF より先に Cognito がトークンを拒んだ（`NotAuthorizedException`）ときは確かめられていないので、
試験用の利用者でサインインして得たアクセストークンを環境変数 `SERVICE_PLATFORM_ACCESS_TOKEN` に入れて流し直す
（トークンは表示しない）。WAF が止めずに ChangePassword が通ったときは、その利用者にパスワードが
足されているので、利用者ごと消す。

## list-mfa-status.sh

```bash
scripts/list-mfa-status.sh dev
```

`list-users` の各ユーザーに `admin-get-user` して `UserMFASettingList` を確認し、
未設定のユーザーを `(未設定)` として表示、最後に未設定件数を出す。

## 前提ツール

`bash` / `aws` (CLI v1 以上) / `python3` (3.11 以上。`tomllib` を使う) / `openssl` /
`sam` / `npm`。`jq` は不要（使っていない）。`shellcheck` は任意。

```bash
command -v shellcheck >/dev/null && shellcheck scripts/*.sh || echo 'shellcheck 未インストールのためスキップ'
```
