# service-platform

顧客向け SaaS の共通基盤（ログイン・利用者管理・機器への割当・サービスの許可）を AWS SAM で構成した設計事例です。昨今のフィッシング、特にパスワードとワンタイムコードを中継するリアルタイムフィッシングや、流出した認証情報を試すリスト型攻撃を踏まえ、**パスワードに頼らないパスキー（Amazon Cognito Essentials）を標準の認証に採用**しました。

認証方式の比較、ADR、設計、構成図、基盤の骨格、実際の Cognito を使った PoC 14 項目までを 1 日で行いました。AI エージェントに調査・設計・実装・検証の役割を分けて任せ、開発者は指示と評価を担当しました。

この公開ツリーには、採用した設計とその判断根拠、独立した PoC、基盤の参考実装を収録しています。ADR の採用は設計上の決定を表し、参考実装が ADR の全機能を備えることや、本番運用への適合を保証するものではありません。

## 見どころ

[ADR-001：認証と許可](Documents/ADR/ADR-001_認証と許可.md) に D1〜D12 の判断と理由をまとめています。

- 専用の Cognito Essentials プールを採用。一般利用者はパスキー、初回と回復は EMAIL_OTP。複数のパスキーを登録し、端末の紛失に備えます。
- 招待制とし、email / email_verified の本人による書換えを禁止します。
- Cognito の sub と業務上の user_id を分離。削除・再招待でも割当と履歴を維持し、古い sub は監査用に保存して認可では拒否します。
- 機器の利用期間・利用者の割当・ログイン記録・サービス許可を API 側で照合。停止と利用期間の終了を認可に反映します。
- RP ID を環境ごとの FQDN に限定。更新トークンの rotation と、有効期間を区切ったセッションを採用します。
- 管理者はパスワード＋TOTP を例外として採用。現在の MFA 設定、台帳の登録時刻と auth_time、別経路での本人確認を組み合わせます。回復は削除・再招待・再登録を一連の手順にします。
- 通知元と添付領域の照合、業務監査と Cognito 直接操作の CloudTrail の対応付けを設計します。

[Cognito PoC の結果](Documents/research/cognito-poc-2026-11.md) は **14 項目中 13 確認・1 否定**です。確認には条件付きの結果を含みます。

- email の属性保護、一時パスワードからの初回変更、複数パスキー、削除後の認証要素の消去、同一 origin の別ビルドによるセッション共有、更新トークンの rotation を確認しました。
- 対象プールでは acr / amr が得られず、期待を否定。管理者のセッションは台帳の mfa_enabled_at と auth_time で判定する設計にしました。
- SDK の ChangePassword ではパスワードを後付けできました。ユーザープールの WAF でこの API を Block し、検証した管理者の初回変更と TOTP 登録が通ることを確認しました。
- 仮想認証器を用いた検証です。実機、CloudFront 経由、同時更新、ハーネスで保存していない観察などの限界を結果文書に明記しています。

## 構成

[構成図（architecture.png）](Documents/images/architecture.png) は ADR の設計を示します。図中の `/camera/` は最初に載せるサービスの配置例で、そのサービス自体はこのリポジトリには含めません。図中の章番号は参照先として使用しません。通知の流れは [notify-flow.png](Documents/images/notify-flow.png) を参照してください。

| スタック | 役割 |
| --- | --- |
| shared | 共通 Lambda レイヤ、共通設定の SSM パラメータ |
| core | Cognito、SES、SQS / DLQ、notifier、添付 S3、運用通知 |
| app-admin-users | 利用者管理の HTTP API と Lambda |
| edge | SPA 用 S3、OAC、CloudFront、SPA フォールバック、Route53 |

設計では core に利用者・機器割当・許可の台帳と監査テーブルを置き、Cognito と CloudFront に WAF を配置します。スタック間の値は `/service-platform/{env}/...` の SSM パラメータで受け渡します。スタック名は `service-platform-{shared|core|edge}-{env}`、アプリは `service-platform-app-{app}-{env}` です。

バックエンドは Python、フロントエンドは Vue 3・TypeScript・Vuetify・Pinia・Amplify を使用します。

## ディレクトリ構成

```text
Documents/
├── ADR/             認証・認可の設計判断
├── research/        Cognito PoC 14 項目の結果と検証の限界
└── images/          構成図・認可フロー・通知フローとソース
infra/core/          認証・通知の SAM スタックとテスト
infra/edge/          SPA 配信の SAM スタックと CloudFront Function
shared/              共通 Lambda レイヤとテスト
apps/admin-users/    利用者管理の参考実装
frontend/            共通 SPA と画面のテスト
templates/app/       サービス追加用の SAM 雛形
scripts/             初期設定・デプロイ・検証用スクリプト
tests/infra/         テンプレートと samconfig の静的検査
poc/cognito/         独立した Cognito 検証用スタックとハーネス
```

## 開発コマンド

```bash
uv sync
make lint       # ruff check + ruff format --check
make test       # pytest + CloudFront Function の Node テスト
make validate   # 残る 4 スタックの sam validate + cfn-lint

cd frontend
npm ci
npm run lint
npm test
npm run build
```

AWS を利用する手順は [scripts/README.md](scripts/README.md)、PoC の再現手順は [poc/cognito/README.md](poc/cognito/README.md) を参照してください。PoC は通常の一括デプロイから独立しています。

## 匿名化について

公開用に固有名詞・ドメイン・アカウント ID 等を架空値へ置き換えています。[匿名化用語集](ANONYMIZATION-GLOSSARY.md) を参照してください。PoC の結果は匿名化前の独立した検証の記録です。**匿名化後の AWS 上での動作は未確認**です。ローカルの検証結果は [公開版の検証記録](Documents/public-validation.md) に記載します。
