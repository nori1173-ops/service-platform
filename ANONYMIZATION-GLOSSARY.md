# ANONYMIZATION-GLOSSARY

公開用に固有名詞と機微値を架空値へ置き換えています。実在の組織・人物・稼働環境との対応表ではありません。匿名化後の AWS 上での動作は確認していません。

| 表記 | 公開例での役割 |
| --- | --- |
| SERVICE-PLATFORM / service-platform / service_platform / 統合サービス基盤 | 顧客向け SaaS 共通基盤、リソース名の接頭辞、Python パッケージ名 |
| devA / devteam | 設定・テストに使う架空の利用者名 |
| example.co.jp / example-cloud.com | メールとサービスの架空ドメイン |
| ses-sender-dev.example-cloud.com | PoC の SES 送信元設定の例 |
| ExamplePowertoolsPython | 任意のログ用 Lambda レイヤの名前。ローカルではフォールバックを使用 |
| 210000000001 | 架空の AWS アカウント ID |
| Z0EXAMPLEZONE0 | 架空の Route53 ホストゾーン ID |
| ap-northeast-1_EXMPLpool1 / exmplclient1 | 架空の Cognito プール ID / クライアント ID |
| aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee | UUID の置換例。値の一致に意味はありません |

テスト用のメール・URL には example 系の値を使用します。AWS のサービス名、公式文書の URL、公開リソースの ID、npm / PyPI のパッケージ名は技術上の名称として保持しています。

残す PNG は [architecture.png](Documents/images/architecture.png) と [notify-flow.png](Documents/images/notify-flow.png) です。構成図は設計を表し、最初に載せるサービスの配置例を含みます。そのサービスは公開ツリーに含めません。
