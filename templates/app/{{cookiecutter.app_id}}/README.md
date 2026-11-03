# {{cookiecutter.app_title}}（`{{cookiecutter.app_id}}`）

service-platform のアプリスタックを作る雛形です。生成後の全体像は リポジトリルートの `README.md`、認証・認可の要件は `Documents/ADR/ADR-001_認証と許可.md` を参照してください。

## 構成

| パス | 内容 |
| --- | --- |
| `template.yaml` | HTTP API / api Lambda / DynamoDB / LogGroup / SSM `/apps/{{cookiecutter.app_id}}/api-domain` |
| `samconfig.toml` | `[default]`（dev）と `[production]`。prod は SSM の**キー名**を上書きする |
| `functions/` | Lambda の `CodeUri`。`api/app.py`（HTTP API）と `batch/handlers/`（バッチ） |
| `statemachine/run.asl.json` | Step Functions を使う場合の定義（既定は未使用） |
| `tests/` | pytest。`conftest.py` が `functions/` を `sys.path` に足す |
| `edge-snippet.yaml` | `infra/edge/template.yaml` に転記する origin / behavior / パラメータ |

## テスト

アプリ直下で実行する。どのアプリも `tests/conftest.py` と `functions/` を同じ名前で持つため、
リポジトリルートから複数アプリをまとめて収集すると import が衝突する。

```bash
cd apps/{{cookiecutter.app_id}}
uv run pytest tests -v
uv run pytest tests --cov=functions --cov-report=term-missing --cov-fail-under=80
sam validate --lint
```

## デプロイ

`sam build` を必ず先に実行する（`requirements.txt` の依存を含めるため）。

```bash
cd apps/{{cookiecutter.app_id}}
sam build --use-container && sam deploy                          # dev
sam build --use-container && sam deploy --config-env production  # prod
```

デプロイ順は shared → core → app → edge。デプロイすると
`/service-platform/{env}/apps/{{cookiecutter.app_id}}/api-domain` が SSM に書かれる。

## 疎通確認

```bash
SECRET=$(aws ssm get-parameter --name /service-platform/dev/edge/origin-verify-secret --with-decryption --query Parameter.Value --output text)
API=$(aws ssm get-parameter --name /service-platform/dev/apps/{{cookiecutter.app_id}}/api-domain --query Parameter.Value --output text)
curl -s "https://$API/api/{{cookiecutter.app_id}}/health" -H "Authorization: Bearer $TOKEN" -H "x-origin-verify: $SECRET"
```

CloudFront 経由の確認は `edge-snippet.yaml` を `infra/edge/template.yaml` へ転記し、
`scripts/deploy-edge.sh dev` を実行してから `https://service-platform-dev.example-cloud.com/api/{{cookiecutter.app_id}}/health` を叩く。

## 生成後の設定

- [ ] `functions/api/app.py` に業務ルートを追加する（設定変更は `auth.require_group(claims, "admin")` で保護）
- [ ] `functions/batch/` を使うなら `template.yaml` の `ScheduleV2` ブロックのコメントを外す
- [ ] `frontend/src/apps/_template/` を `frontend/src/apps/{{cookiecutter.app_id}}/` にコピーして書き換える
- [ ] サービス固有の設計資料を作成する
- [ ] リポジトリルート `README.md` のアプリ一覧に追記する
