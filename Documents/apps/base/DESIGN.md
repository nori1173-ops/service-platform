# 土台 API 設計書（base）

更新日: 2026-11-05。実装済みの本人 API・通信機の入口・ウォームアップを記す。[セキュリティ設計](../../SECURITY-DESIGN.md)、[運用手順](../../RUNBOOK.md)、[ADR-001](../../ADR/ADR-001_認証と許可.md) を併せて読む。

## 目的と構成

本人の状態・割り当て、admin 本人の TOTP 登録完了、通信機ログイン・最新値・サービス一覧を提供する。admin による他人への操作は admin-users が持つ。独自のテーブルは作らず、core の台帳・監査を使う。

| 項目 | 内容 |
| --- | --- |
| スタック | service-platform-app-base-{env} |
| API | HTTP API、接頭辞 /api/base、JWT authorizer、既定 rate 20／burst 40 |
| Lambda | Python 3.13、256 MB、30 秒、Handler: base_api.app.handler |
| 処理 | functions/base_api/app.py、me.py、ledger_writes.py、clients.py |
| SSM の読み取り | /cognito/issuer、/cognito/client-id、/cognito/user-pool-id、/shared/layer-arn、/ledger/table-name、/audit/table-name（/service-platform/{env} 配下） |
| SSM の出力 | /service-platform/{env}/apps/base/api-domain |
| edge | api-base origin、/api/base/* behavior、x-origin-verify |
| ログ | Lambda・API アクセスログとも 365 日 |

## API 一覧

全ルートに JWT と origin-verify が必要。成功応答は shared/response.py の `ok / data / error / meta`。表は data の中身を示す。

| メソッド・パス | 条件 | 応答 |
| --- | --- | --- |
| GET /me | identity.require_user(allow_suspended=True)。本人だけ、停止中も可 | user_id、email、status、restore_requires、kind、org_name、is_admin、admin_state、mfa_registered |
| POST /me/totp-registered | 同上に加え、admin グループ・pending の本人、Cognito の現在状態と TOTP を確認 | admin_state=awaiting |
| POST /me/passkey-mfa | D9 を満たす本人。現在の TOTP と登録済みパスキーを確認 | WebAuthn MFA を有効化 |
| GET /health | identity.require_user()。台帳に登録された有効な利用者 | status=ok |

GET /me の restore_requires は本人の台帳の値を返し、項目が無ければ null。再招待後の確認待ちは reinvite。is_admin はトークンの admin グループの表示で、D9 を通ることを保証しない。mfa_registered は mfa_enabled_at の有無だけ。時刻の値・contact・他の利用者の情報は返さない。

未登録・古い sub・revoked_at は 403 USER_NOT_REGISTERED。停止中は /health を 403 USER_SUSPENDED にする。/me と /me/totp-registered の例外でも無効の印を迂回しない。画面は取得した状態を使って、停止中・準備中・確認待ちの人を /account へ案内する。

## TOTP 登録完了の処理

1. トークンに admin、台帳に cognito_username、admin_state=pending があることを確認する。
2. AdminGetUser と AdminListGroupsForUser で存在・Enabled・同じ sub・admin・UserMFASettingList の SOFTWARE_TOKEN_MFA を確認する。
3. AdminUserGlobalSignOut でそれまでのセッションを失効させる。失敗時は台帳を変えず 503。
4. 失効の後の現在時刻を mfa_enabled_at（epoch 秒）にし、PROFILE を awaiting へ変更する。同じ sub と pending を条件に、version を増やして last_op_id を書く。
5. 変更と admin.totp_registered の監査を同じトランザクションで書く。成功は 200、条件が変わっていれば ADMIN_NOT_PENDING。

成否は shared/transactions.write が同じ token で再試行し、PROFILE の last_op_id を強い整合で読み直して確認する。Cognito または台帳の一時的失敗・結果不明は 503 TEMPORARILY_UNAVAILABLE。失効後に台帳更新が失敗しても、pending が続く間は期限内の手元の ID トークンで再実行できる。

この API は有効化しない。本人以外が別経路で登録を確認して admin-users（最初と緊急時は scripts）で active にする。本人は mfa_enabled_at より後にパスワード＋TOTP でサインインし直す。

| HTTP | code | 意味 |
| --- | --- | --- |
| 401 | UNAUTHORIZED | クレームが無い・ID トークンでない |
| 403 | ORIGIN_FORBIDDEN | CloudFront 経由の検査に失敗 |
| 403 | USER_NOT_REGISTERED / USER_SUSPENDED | 台帳での拒否 |
| 403 | NOT_ADMIN / ADMIN_NOT_PENDING / ADMIN_MFA_MISSING | 登録完了を受け付ける条件を満たさない |
| 503 | TEMPORARILY_UNAVAILABLE | Cognito・台帳の処理を確定できない |

## パスキー MFA の有効化

パスキー登録後に POST /me/passkey-mfa を呼ぶ。D9 と現在の TOTP を確認し、要求の監査を保存してから WebAuthnMfaSettings.Enabled=true を設定する。TOTP は有効・優先のまま維持する。パスキー未登録は 409 PASSKEY_REQUIRED、ほかの一時的失敗は 503。台帳の admin_state と mfa_enabled_at は変えない。

登録前の有効化は Cognito の InvalidParameterException で拒否されたため、登録 → 有効化の順にした。設定だけ失敗した場合、画面は登録を重複させず設定を再試行する。

## health と warmer

/health は公開のヘルスチェックではなく、認証と台帳を含む疎通確認。admin グループは要求しない。

EventBridge Scheduler が dev / prod とも 5 分ごとに ApiFunction を直接呼ぶ。入力は `{"warmer":true,"slot":0}`、FlexibleTimeWindow は OFF、最大イベント年齢 60 秒、再試行 0 回。専用ロールは呼び出し元の ScheduleGroup・AWS アカウントを条件にし、対象 Lambda の InvokeFunction だけを許す。

handler の一番外側に @warmer.warm、その内側に @LambdaLogger.contextualize、@origin_verify.origin_verify を置く。トップレベルの warmer が真偽値の true の場合だけ早期応答し、業務処理・業務ログ・認証を迂回する。HTTP body 内の warmer は該当しない。応答は warmer と slot。sleep_ms は 0〜2000 ミリ秒に収める。Lambda の START / END / REPORT は残る。admin-users はウォームアップ対象外。

## IAM・検証・配備

台帳への GetItem / UpdateItem、監査への PutItem、対象プールへの AdminGetUser / AdminListGroupsForUser / AdminUserGlobalSignOut / AdminSetUserMFAPreference、origin-verify の取得に必要な権限に絞る。通信機の台帳の GetItem / Query / PutItem / UpdateItem / DeleteItem とトランザクション、デバイス活動台帳の読み取りと SSM の解決を追加済み。DeviceBackendApiBaseUrl は samconfig の String パラメータ。

apps/base/tests が本人の参照・停止の例外・TOTP 登録完了・health を検査し、shared/tests と tests/infra/test_base_template.py がガード・トランザクション・warmer・IAM・Scheduler を検査する。全体は make lint / make test。配備順と環境の指定は RUNBOOK に従い、shared → core → base → edge → frontend。レイヤ更新後は base も再配備する。

## 通信機の入口（、実装済み）

| API (接頭辞 /api/base) | 条件と応答 |
| --- | --- |
| GET /me/assignments | 有効な本人。自分の今有効・将来・終了した割り当て (人宛と、所属の経路のグループ宛) と、期間の許可。終わった割り当てには製造番号・許可を返さない |
| GET /me/devices | 有効な本人 。今有効な割り当て (人宛・グループ宛) と、access が everyone の稼働中の通信機 (GSI1 の DEVICE、結果整合) を 1 通信機 1 行で。表示だけで認可に使わない。製造番号は本人の控えだけ。各行の service_count (今有効な許可のサービスの数) は画面の近道  の判定だけに使う |
| POST /devices/login | (1) 稼働中の P と (2) P を扱う根拠 (人宛・グループ宛の割り当てか everyone。DESIGN 6.7)。account・serial・pin・mode で照合し、LOGIN (根拠の種類・ID・世代) と未設定の製造番号 (人宛は割り当て、グループ宛と everyone は本人の控え)・監査を同じトランザクションで書く。LOGIN の置き換えは読んだ版が条件 |
| POST /devices/refresh | (1)(2)。前回値で秘密を確認してから最新値の受付・読取り要求。202 と受付ごとの poll_token |
| GET /devices/{account}/refresh-availability | (1)(2)。固定 48 時間以内の活動報告があれば不可。読み出しの失敗も不可 |
| GET /devices/{account}/services | (1)〜(3)。12 時間以内で、根拠が今の根拠の集合に含まれる LOGIN を確認し、今有効な許可と /camera/ 等の起動先 |

認可は shared/access.py を使い強い整合で読む。書く直前にも根拠の行 (割り当て・所属) と P の version・status・access を検査する。通常の照会失敗は通信機ごとに 10 分に 5 回で 10 分、利用者ごとに全通信機で 60 分に 10 回で 60 分停止 。待機は 10 分・最大 65 回、poll_token と HMAC が一致する受付だけ失敗回数の免除を認め、8 秒未満は 429。別の利用者の受付は 409 IN_PROGRESS。秘密と poll_token 自体を保存しない。専用の login ルートだけ rate 5 / burst 10。

DeviceActivityTableNameParameter は SSM の値ではなくキー名を String で受け、実行時に GetParameter で解決する。SSM・台帳を読めなければ GUARD_UNAVAILABLE。初回ログイン前に通信機の設定を読み取る。詳細な入力・応答・エラーは [base README](../../../apps/base/README.md)、操作は RUNBOOK。
