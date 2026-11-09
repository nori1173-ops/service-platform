# 土台（`base`）

利用者本人が呼ぶ土台の API (`/api/base/*`、スタック `service-platform-app-base-{env}`)。全体像は
[API の設計](../../Documents/apps/base/DESIGN.md)、決めは [ADR-001](../../Documents/ADR/ADR-001_認証と許可.md)
を参照する。admin の操作は `apps/admin-users` に置き、ここには置かない (計画の判断 2)。

## API

| パス | メソッド | 呼べる人 | 内容 |
| --- | --- | --- | --- |
| `/health` | GET | 台帳に登録された有効な利用者 | `{"status": "ok"}` |
| `/me` | GET | 本人 (停止中も可。ADR D9 の例外 2) | `user_id`、`email`、`status`、`restore_requires` (台帳の値。無ければ `null`)、`kind`、`org_name`、`is_admin` (クレームの admin)、`admin_state`、`mfa_registered` (`mfa_enabled_at` の有無だけ) |
| `/me/passkey-mfa` | POST | D9 を通る有効な admin 本人 | パスキーの登録後に TOTP を有効・優先のまま WebAuthn MFA を有効化する。要求の監査 `admin.passkey_mfa_requested` を先に書き、Cognito の結果を `admin.passkey_mfa_enabled` (ok / failed、失敗時は code) に最善努力で追記する。パスキー無しは 409 `PASSKEY_REQUIRED`、それ以外の失敗は 503。台帳の状態・登録時刻は変えない |
| `/me/totp-registered` | POST | admin グループで admin の状態が準備中の本人 (停止中も可。例外 1) | Cognito の現在の状態 (有効、admin グループ、TOTP) を確かめ、全セッションを失効させ、自分の台帳の行を確認待ちにして `mfa_enabled_at` を書く。監査 `admin.totp_registered` |
| `/me/assignments` | GET | 有効な利用者 | 自分の割り当て (人宛と、本人の所属の経路のグループ宛。`state` = `current` / `upcoming` / `ended`、`target_type` = `user` / `group`、グループ宛は `group_id`・`group_label` (経路の名前を「 > 」でつないだもの)) と、終わっていない割り当ての利用期間の許可 (今有効なものと、これから始まるもの)。ほかの利用者の割り当ては読まない |
| `/me/devices` | GET | 有効な利用者 | 通信機ログインの一覧 。今有効な割り当て (人宛・グループ宛) と、access が everyone の稼働中の通信機 (稼働中の利用期間の指しの写し、GSI1 の `DEVICE`。結果整合) を通信機アカウントで 1 行にする (割り当てを優先)。並びは割り当て → everyone。項目 `account`・`device_period_id`・`org_name`・`basis` (`user` / `group` / `everyone`)・`assignment_id`・`group_id`・`group_label`・`valid_from`・`valid_to`・`serial` (人宛は割り当ての行、グループ宛と everyone は本人の控え)・`service_count` (今有効な許可のサービスの数。同じサービスは 1 つ、定義に無いサービスは数えない。画面の近道  の判定だけに使う)。表示だけで、認可には使わない |
| `/devices/login` | POST | 有効な利用者 + 条件 (1)(2) | 本文 `account`・`serial` (8 桁)、`pin` (4 桁)、`mode` (`previous` / `latest`、既定 `previous`)。最新値の待機中は `acceptance_id`・`poll_token` も。利用者ごとの失敗回数 → 条件 (1)(2) → 本人のログイン記録を読む → 通信機ごとの失敗回数 → device-backend の照会 → ログイン記録 (12 時間。根拠の種類・ID・世代) と監査 `device.login` (`detail.basis`) → 製造番号がまだ無ければ控える (人宛は割り当ての行、グループ宛と everyone は本人の `SERIAL#`)。応答は `account`・`method`・`location_name`・`observation`・`devices[]`・`services[]` |
| `/devices/refresh` | POST | 有効な利用者 + 条件 (1)(2) | 本文 `account`・`serial`・`pin`。最新値の開始 (運用中のガード → 前回値の照会で暗証番号を確かめる → 受付の記録 10 分 → 読取り要求)。202 で `acceptance_id` と `poll_token` (1 回だけ) を返す。本人の受付が期限内なら読取りを要求し直さずに新しい `poll_token` を返す (`resumed`)。監査 `device.refresh_start` |
| `/devices/{account}/refresh-availability` | GET | 有効な利用者 + 条件 (1)(2) | `available`・`reason` (`in_operation` / `guard_unavailable`)・`in_progress`。根拠の無い人には答えない (everyone の通信機では割り当ての無い人にも答える) |
| `/devices/{account}/services` | GET | 有効な利用者 + 条件 (1)(2)(3) | 今有効な許可と起動先 (`/camera/?account=...`)、ログイン記録の要約の現場名・手段・期限 |

- 全ルートが shared の `identity.require_user` で台帳の利用者を確かめる。再招待の前の sub (無効の印) は 403 `USER_NOT_REGISTERED`、停止は 403 `USER_SUSPENDED`。準備中・確認待ちの admin (クレームの admin か台帳の role=admin で、admin の状態が有効でない) は、`/me` と `/me/totp-registered` のほかは 403 `ADMIN_NOT_ACTIVE`。
- `/me/totp-registered` の拒否は 403 (`NOT_ADMIN`、`ADMIN_NOT_PENDING`、`ADMIN_MFA_MISSING`)。失効・台帳更新の失敗は 503 `TEMPORARILY_UNAVAILABLE` で、呼び直せる。WebAuthn MFA はパスキー未登録時に Cognito が拒否するため、登録後の `/me/passkey-mfa` で有効にする。この API は SDK のモデルに WebAuthnMfaSettings が無い場合は shared の SigV4 呼び出しを使う。
- 通信機の条件 (ADR D4 / D14) は shared の `access` で確かめる。稼働中の利用期間か根拠 (人宛・グループ宛の割り当てか everyone) が無ければ 403 `DEVICE_NOT_ASSIGNED` (区別しない)、ログイン記録が無い・期限切れ・記録の根拠が今の根拠の集合に無ければ 403 `DEVICE_LOGIN_REQUIRED`。

### 通信機ログインの決め

| 事項 | 決め |
| --- | --- |
| 順番 | 入力の検査 (400 `VALIDATION_ERROR`) → 利用者ごとの失敗回数 (止めていれば 429 `USER_PIN_LOCKED`。通信機のことを何も知らせない) → 条件 (1)(2) (根拠の無い人は device_backend を呼ばない) → 本人のログイン記録を強い整合で読む → 通信機ごとの失敗回数 (止めていれば 429 `PIN_LOCKED`) → device-backend の照会。止めているかどうかを根拠の無い人に知らせないよう、通信機ごとの失敗回数は根拠の後に見る |
| ログイン記録 | 根拠 (`basis` = `user` / `group` / `everyone`) と ID (`assignment_id`、group は `group_id`・`membership_ids`、everyone は `access_gen`) を書く。置き換えは、初めに読んだ記録の `last_op_id` (無ければ行が無いこと) を条件にする。根拠の始まりの大小では縛らない。書くトランザクションに根拠の行 (割り当て、所属の `membership_id`、利用期間の version と access) の条件を入れ、根拠で落ちたら根拠を確かめ直して 403 `DEVICE_NOT_ASSIGNED` か 409 `CONFLICT`、記録か控えで落ちたら 409 `CONFLICT` (並行するログイン) |
| 照会の失敗 | device_backend の 401 は 403 `PIN_MISMATCH`、403 は 403 `SERIAL_MISMATCH` (401 にすると画面がサインアウトするため)。それ以外と接続の失敗は 502 `DEVICE_BACKEND_UNAVAILABLE` (数えない) |
| 失敗回数 | 台帳 `DEVICE#{account}` / `PINFAIL`。最初の失敗から 10 分の窓で 5 回失敗したら 10 分止める。最新値の開始の暗証番号違い・製造番号違いも数える。成功で消す (別の要求が直前に止めたときは消さない)。止めるのは、数えた窓 (数え始め) と回数がそのままのときだけ (成功と作り直しを挟んだ古い 5 回目では止めない)。止めたときは監査 `device.pin_locked` (`detail.scope=device`) |
| 利用者ごとの失敗回数 | 台帳 `USER#{user_id}` / `PINFAIL` (ADR D14)。全通信機を合わせて 60 分の窓で 10 回失敗したら 60 分止める (429 `USER_PIN_LOCKED`)。通信機ごとと同じ失敗で数える (待機中の免除は両方とも数えない)。成功では消さない (時間で解ける)。止めたときは監査 `device.pin_locked` (`detail.scope=user`) |
| 最新値の開始と待機中の照会 | 開始は前回値の照会で暗証番号を確かめてから受け付ける (違えば数えて 403、前回値が無ければ 409 `NO_PREVIOUS_SETTINGS`)。受付ごとに 32 バイトの乱数の `poll_token` を応答で 1 回だけ返し、記録 (`REFRESH`) には `pin_mac = HMAC-SHA256(key=poll_token, msg=account‖pin)` と `acceptance_id` だけを持つ。`mode=latest` の 401 は「報告がまだ」と「暗証番号の誤り」を区別できない  ので、受付の本人が同じ `acceptance_id`・`poll_token`・暗証番号 (HMAC が一致) で照会したときだけ、受付ごとに 65 回まで数えない。照会の間隔が 8 秒未満なら 429 `POLL_TOO_SOON` (同じ受付への同時の照会で条件付きの更新に落ちた側も、読み直して間隔の違反なら 429。device_backend を呼ばず、数えない)。合わない照会は普通に数える。受付の記録は、合った照会の成功か読取り要求の失敗のときだけ、`acceptance_id` が一致する行を消す。ほかの利用者の受付がある間の開始は 409 `IN_PROGRESS` |
| ログイン記録と製造番号 | ログイン記録・製造番号 (人宛は割り当ての行、グループ宛と everyone は本人の `SERIAL#{device_period_id}`。まだ無いときだけ)・監査を 1 つのトランザクションで書く。条件は device_backend の照会の後、書く直前に取り直した時刻と、根拠の行 (人宛・グループ宛の割り当てが今有効、所属の `membership_id` が読んだまま)、条件 (1) で読んだ親の利用期間 (稼働中で version が読んだまま。everyone は access も everyone のまま。人宛で製造番号を書くときは version を上げる)。落ちたときの扱いは上の「ログイン記録」の行のとおり |
| 最新値のガード | 外部のデバイス活動台帳 (`service-platform-device-activity-{env}`) に固定の 48 時間以内の `last_seen` があれば 409 `IN_OPERATION`。表の名前は SSM のキー (`DeviceActivityTableNameParameter`) から実行時に読む。キーが空・SSM に無い・表を読めない・読めない `last_seen` があれば 503 `GUARD_UNAVAILABLE` (fail-closed)。二重実行は 409 `IN_PROGRESS`。通信機から応答が無ければ 404 `NO_RESPONSE` |
| 暗証番号 | device-backend への `X-Example-Pin` ヘッダと HMAC にだけ使う。台帳・監査・ログ・応答・例外のメッセージに入れない (テストで縛る)。`poll_token` も保存しない。リダイレクトは追わない |
| 接続先 | samconfig の `DeviceBackendApiBaseUrl` (String)。dev も本番の device-backend に向ける (検証機器が本番にだけある)。未設定は届かない既定値 |
| スロットリング | `POST /api/base/devices/login` だけ専用のルートで rate 5 / burst 10。ほかは rate 20 / burst 40 |

## 構成

| パス | 内容 |
| --- | --- |
| `template.yaml` | HTTP API / api Lambda / LogGroup / SSM `/apps/base/api-domain`。自前のテーブルは持たず、core の台帳と監査を使う |
| `samconfig.toml` | `[default]`（dev）と `[production]`。prod は SSM の**キー名**を上書きする |
| `functions/base_api/` | `app.py` (ルート)、`me.py` (自分の状態と TOTP の登録、割り当ての一覧)、`devices.py` (通信機ログイン・最新値・サービスの一覧)、`device_writes.py` (ログイン記録と製造番号の書き込み)、`errors.py`、`device_backend_client.py` と `device_settings.py` (外部通信機 API と要約処理)、`pinfail.py` (失敗回数)、`refresh_guard.py` (最新値のガードと受付)、`ledger_writes.py` (台帳と監査の書き込み)、`clients.py`、`clock.py` |
| `tests/` | pytest。台帳・監査・デバイス活動台帳・SSM は moto、Cognito と device-backend は薄い偽物。値は `device_data.py` |
| `edge-snippet.yaml` | `infra/edge/template.yaml` に転記済みの origin / behavior / パラメータの写し |

## テスト

```bash
uv run pytest apps/base -q
uv run pytest apps/base --cov=apps/base/functions --cov-report=term-missing
```

## デプロイ

```bash
cd apps/base
sam build --use-container && sam deploy                          # dev
sam build --use-container && sam deploy --config-env production  # prod
```

デプロイ順は shared → core → app → edge（設計 3.3）。デプロイすると `/service-platform/{env}/apps/base/api-domain` が
SSM に書かれ、`scripts/deploy-edge.sh {env}` が読んで CloudFront に `/api/base/*` を足す。

## 疎通確認

```bash
TOKEN=$(uv run python scripts/get_id_token.py dev {メールアドレス})
curl -s "https://service-platform-dev.example-cloud.com/api/base/me" -H "Authorization: Bearer $TOKEN"
```
