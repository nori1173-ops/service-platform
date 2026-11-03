# ユーザー管理（`admin-users`）

service-platform の利用者管理 API の参考実装です。全体像は [README](../../README.md)、認証・認可・回復の設計要件は [ADR-001](../../Documents/ADR/ADR-001_認証と許可.md) を参照してください。

`admin` グループの管理者が、サインアップ済みユーザーの一覧・admin 付与/解除・
無効化/有効化・パスワードリセット・削除を画面から行うための API を提供する。

## 構成

| パス | 内容 |
| --- | --- |
| `template.yaml` | HTTP API / api Lambda / LogGroup / SSM `/apps/admin-users/api-domain` |
| `samconfig.toml` | `[default]`（dev）と `[production]`。prod は SSM の**キー名**を上書きする |
| `functions/admin_users/app.py` | HTTP API のルート（`Handler: admin_users.app.handler`） |
| `functions/admin_users/cognito.py` | Cognito 管理 API の薄いラッパ（ページング・`sub` 検索） |
| `tests/` | pytest + moto（`cognitoidp`）。`conftest.py` が `functions/` を `sys.path` に足す |
| `edge-snippet.yaml` | `infra/edge/template.yaml` へ転記済みの origin / behavior / パラメータの写し |

DynamoDB テーブル・バッチ・ステートマシン・通知は持たない（雛形から削除済み）。
操作の証跡は Lambda の構造化ログ（CloudWatch Logs、保持 365 日）に残す。

**ハンドラのモジュール名は雛形の `api.app` ではなく `admin_users.app`。** 全アプリの
テストを 1 つの pytest セッションで走らせると `api` という同名パッケージが sys.modules で
衝突し、先に読まれた別アプリのモジュールを掴むため。

## テスト

```bash
cd apps/admin-users
uv run pytest tests -v
uv run pytest tests --cov=functions --cov-report=term-missing --cov-fail-under=80
sam validate --lint
```

リポジトリ全体は `uv run pytest`（`pyproject.toml` の `pythonpath` に
`apps/admin-users/functions` を登録済み）。

## デプロイ

`sam build` を必ず先に実行する。

```bash
cd apps/admin-users
sam build --use-container && sam deploy                          # dev
sam build --use-container && sam deploy --config-env production  # prod
```

デプロイ順は shared → core → app → edge。デプロイすると
`/service-platform/{env}/apps/admin-users/api-domain` が SSM に書かれる。
edge 側（origin / behavior）と `scripts/deploy-edge.sh` は追記済みなので、
アプリのデプロイ後に `scripts/deploy-edge.sh {env}` を流せば経路が繋がる。

## 疎通確認

```bash
SECRET=$(aws ssm get-parameter --name /service-platform/dev/edge/origin-verify-secret --with-decryption --query Parameter.Value --output text)
API=$(aws ssm get-parameter --name /service-platform/dev/apps/admin-users/api-domain --query Parameter.Value --output text)
curl -s "https://$API/api/admin-users/users" -H "Authorization: Bearer $TOKEN" -H "x-origin-verify: $SECRET"
```

`TOKEN` は `admin` グループのユーザーの ID トークン（`uv run python scripts/get_id_token.py dev {email}`）。
全ルートが `admin` 専用なので、一般ユーザーのトークンでは 403 が返る。
CloudFront 経由の確認は `https://service-platform-dev.example-cloud.com/api/admin-users/users`。

## 運用の設計要件

管理者の招待・TOTP 登録・別経路確認は ADR D9、紛失時の削除・再招待・再登録は D8 に従います。自分自身への権限変更・無効化・削除は API で拒否し、管理者の最低人数を維持する更新制約を設けます。
