# Cognito PoC

[ADR-001](../../Documents/ADR/ADR-001_認証と許可.md) の PoC 14 項目を、独立した AWS 検証環境で確かめるための使い捨ての一式。結果は [検証記録](../../Documents/research/cognito-poc-2026-11.md) にある。

本番の資源と既存のユーザープールには触らない。終わったら必ず `teardown.sh` でスタックを消す。

## 中身

| ファイル | 役目 |
| --- | --- |
| `template.yaml` | PoC スタック (ユーザープール Essentials、クライアント 2 つ (rotation の有無)、admin グループ、ユーザープールの web ACL)。タグは Project=service-platform、Name=PoC |
| `deploy.sh` / `teardown.sh` | スタックの作成・更新と削除。作成後に Outputs を `web/site/config.raw.json` に書く |
| `web/` | 静的ページ (`/` と `/camera/`) とバンドルの元。aws-amplify 6.20.0 と @aws-sdk/client-cognito-identity-provider を esbuild で 1 本ずつにまとめる (`/camera/` は別のバンドル) |
| `run_poc.py` | boto3 + Playwright。項目ごとの関数。`--only N` で 1 項目だけ実行、`--list` で一覧 |

## 前提

- 独立した検証用アカウントの AWS 資格情報、リージョン ap-northeast-1。
- 検証済みの SES ドメイン (送信元。既定は `ses-sender-dev.example-cloud.com`)。SES の送信権限と宛先の検証条件を満たすこと。
- Python は boto3 と playwright の入った環境 (chromium を Playwright で入れておく)。boto3 1.42 系には AdminGetUserAuthFactors が無いので、`run_poc.py` は SigV4 で直接呼ぶ。
- メール受信には標準ライブラリの IMAP over TLS を使う。POC_IMAP_HOST / POC_IMAP_USER / POC_IMAP_PASSWORD を環境変数で指定する。POC_IMAP_PORT は既定 993、POC_IMAP_MAILBOX は既定 INBOX。設定がない場合はメールコードを必要とする項目を実行できない。資格情報は対話入力等で設定し、リポジトリには保存しない。
- 環境変数 `POC_TEST_MAIL` に、検証者が管理する専用のテスト宛先を入れる。招待とメールのコードは実際にこの宛先へ送られる。ほかの宛先は使わない (利用者名がメールアドレスなので、同時に 1 人しか作れない。項目の順でお客様役と admin 役を入れ替える)。
- 宛先は承認済みのテスト用の 1 つだけにする。個人のアドレスをリポに入れないので、コードでは強制していない。流す人が `POC_TEST_MAIL` の中身を確かめる。

## 動かし方

```bash
./deploy.sh                                   # RP ID は既定で localhost
(cd web && npm ci && npm run build)
export POC_TEST_MAIL=<テスト用の宛先>
python run_poc.py --list
python run_poc.py                             # 全部 (ORDER の順)。13 は最初の呼び出しから 15 分以上たってから
python run_poc.py --only 13                   # 13 だけ後で
python run_poc.py --cleanup                   # PoC の利用者を消す
./teardown.sh
```

- ブラウザは headless の chromium で、`http://localhost:8765` の静的ページを開く。パスキーは CDP の仮想認証器 (`WebAuthn.addVirtualAuthenticator`、ctap2、UV あり) で、`pc` (internal) と `phone` (hybrid) と `admin` (internal) を付け替えて使う。
- ブラウザの状態 (localStorage と仮想認証器の鍵) は `out/state.json` に、結果は `out/results.json` に、呼び出しの時刻は `out/events.json` に残る。`out/` は git の対象外で、PoC の利用者のトークンと鍵を含むので、`teardown.sh` で消す。
- 項目は前の項目の状態を使う (例: 10 は 12 で登録した `phone` のパスキーを使う、5 は 4 の後)。順は `ORDER` を見る。
- 14 の後半 (`14waf`) は、スタックのパラメータ `ChangePasswordRuleAction` を Block にして確かめ、Count に戻す (スタックの更新に 1 分ほどかかる)。
- UpdateUserPool は指定しない設定を既定に戻すので、このハーネスでは呼ばない。

## 単独で流すとき

`--only N` だけでは、補足の項目の結果は揃わない。補足も別に流し、先に要る状態を作っておく。

| 項目 | 一緒に流すもの | 先に要る状態 |
| --- | --- | --- |
| 6 | `6b` (AcrConfiguration、不正な値) | TOTP を登録した admin 役 (5 の後) |
| 10 | なし | `phone` のパスキー (12 の後) |
| 11 | `11b` (本パスワードを決めた後) | TOTP を登録した admin 役 (5 の後)。`11b` は 11 の後 |
| 12 | `12b` (消した鍵の assertion)、`12c` (消した後の候補) | お客様役。`12b` と `12c` は 12 の後 |
| 14 | `14waf` (Block の間のお客様役)、`14wafadmin` (Block の間の admin 役) | `phone` のパスキー (12 の後)。`14waf` は 14 の後。`14wafadmin` は 11b の後 (TOTP を外した admin 役) |
| 13 | なし | 12 と 14 を流した同じ `out/events.json` |

CloudTrail の待機の目安:

- 13 だけが CloudTrail を引く。対象は 12 (パスキーの登録と削除) と 14 (ChangePassword) の呼び出しで、どちらの呼び出しからも 15 分以上たってから流す。
- 今回は、最初の呼び出しから 24 分後、最後の対象の呼び出しから 18 分後に揃っていた。届くまでの時間は測っていない。
- 見つからないときは、時間をおいて 13 を流し直す。すぐに「記録されない」と判定しない。

## ハーネスの記録と検証の限界

結果文書は、保存した結果・手動観察・推論を分けている。公開版の IMAP 接続は汎用設定に変更した。変更後の AWS を含む再実行は行っていない。

- 項目 10: `force_refresh_2` と `web_client_force_refresh` は返り値を捨てる。トークンが無くても `ok: true` になるので、この 2 つは「呼び出しがエラーなく返った」までしか言えない。rotation の本体は更新後のトークンを読んでいる。
- 項目 13: ハーネスはイベントの一部 (件数、`userIdentity` の種類、要求のキー名、`additionalEventData`、sub の対応など) だけを残す。`readOnly`、秘密値が隠されること、`credentialId` の有無、`principalId`、`resources` は残らないので、CloudTrail の lookup-events を直接読む。
- 項目 12: 候補 (`allowCredentials`) の不明な項目を `unknown` に潰す。transports、ID の長さ、同じ項目かは記録に残らない。
- 項目 6 (`6b`): DescribeUserPool がエラーでも空のプールとして扱い、`AcrConfiguration` が無いと記録する。応答が成功かを確かめていない。

`/camera/` は同一 origin の別バンドルを検証するためのサンプルページで、サービスの実装を含まない。実機の認証器、CloudFront 越し、同時更新などの検証範囲は結果文書を参照する。
