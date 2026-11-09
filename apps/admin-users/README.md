# ユーザー管理（`admin-users`）

service-platform のアプリスタック。全体像は [API の設計](../../Documents/apps/admin-users/DESIGN.md)、
認証と許可の決めは [ADR-001](../../Documents/ADR/ADR-001_認証と許可.md)、
追加手順は [API の設計](../../Documents/apps/admin-users/DESIGN.md) を参照する
([ユーザー管理 設計書](../../Documents/apps/admin-users/DESIGN.md) に現行 API と運用を記載)。

有効な admin が、利用者 (お客様・社員・admin) を台帳の user_id で招待・停止・再招待・有効に戻す API を提供する。

## API (`/api/admin-users` の後)

| パス | メソッド | 内容 |
| --- | --- | --- |
| `/health` | GET | `{"status": "ok"}` |
| `/users` | GET | 一覧 (台帳 + Cognito の状態)。Cognito にいて台帳に無い利用者は `in_ledger: false` で出す |
| `/users` | POST | 招待。本文 `email`・`kind` (`customer` / `employee`)・`role` (`user` / `admin`)・`org_name` (お客様は必須)・`contact`・`note`。お客様と社員はパスワード無しで招待のメールを送る。admin はメールを送らず、一時パスワードを応答で 1 回だけ返す |
| `/users/{user_id}` | GET | 詳細 (連絡先、パスキー・パスワード・メールのコードの有無、TOTP、`mfa_enabled_at`、割り当て) |
| `/users/{user_id}/profile` | PUT | `org_name`・`contact`・`note` |
| `/users/{user_id}/email` | PUT | email の変更 (確認済みにする) |
| `/users/{user_id}/suspend` | POST | 停止 (台帳 → 無効化 → 全セッション失効)。本文 `reason` |
| `/users/{user_id}/restore` | POST | 有効に戻す。本文 `verified_method` (`phone` / `in_person` / `video` / `other`)・`note`。再招待の後はパスキーの登録が要る。admin は `admin-activation` を使う |
| `/users/{user_id}/reinvite` | POST | 再招待 (ADR D8 手順 2 / 4)。本文 `reason`・`role` (`admin` / `user`、省略時は現在の役割。admin は社員だけ)・`email` (宛先の変更)・`verified_before` (`{method, note}`)。台帳は停止のまま。admin は一時パスワードを応答で 1 回だけ返し、admin の状態を準備中に戻す |
| `/users/{user_id}/admin-activation` | POST | 確認待ちの admin を有効にする。本文 `method`・`note`。再招待の後は台帳の状態も戻す |
| `/users/{user_id}/temporary-password` | POST | admin の一時パスワードを出す (応答で 1 回だけ) |
| `/users/{user_id}/admin` | PUT | `{"admin": false}` で admin を外す。パスワードと TOTP は Cognito に残る。完全に外すには role=user で再招待。付けるときは社員を招待するか、再招待で role=admin を選ぶ |
| `/device-periods` | GET | 利用期間の一覧。`status=active` (既定) / `all`。応答 `data.device_periods` |
| `/device-periods` | POST | 開始。本文 `account` (半角数字 8 桁)・`org_name` (1〜100 文字)・`note` (500 文字まで)。201 |
| `/device-periods/{id}` | GET | 利用期間の詳細。応答に `assignments` と `grants` を含む |
| `/device-periods/{id}/end` | POST | 一括終了。開いている割り当てと逆引き・許可を同時刻で終え、属する LOGIN と CURRENT を条件付きで消す |
| `/device-periods/{id}/assignments` | POST | 割り当て。本文 `user_id`・`valid_from` は必須。`valid_to` だけ省略 / null 可。停止中の利用者と admin 自身も指定可。201 |
| `/device-periods/{id}/assignments/{assignment_id}/end` | POST | 割り当てと逆引きを終え、同じ割り当ての LOGIN を条件付きで消す |
| `/device-periods/{id}/grants` | POST | 許可。本文 `service` (`camera`: カメラ画像判定)・`starts_at` は必須。`ends_at` だけ省略 / null 可。201 |
| `/device-periods/{id}/grants/{grant_id}/end` | POST | 許可を終える |

- 全ルートが shared の `identity.require_user` と `admin_guard.require_admin` (ADR D9) を通る。拒否は 403 (`USER_NOT_REGISTERED`・`USER_SUSPENDED`・`ADMIN_NOT_ACTIVE`・`ADMIN_SESSION_STALE`・`ADMIN_MFA_MISSING`)。
- 自分自身への停止・再招待・admin を外す・有効化・一時パスワードは 403 `SELF_OPERATION_FORBIDDEN`。最後の有効な admin の停止・再招待・admin を外すのは 409 `LAST_ADMIN`。有効な admin の集合 (台帳の `ADMIN#SET` / `ACTIVE` の `members`) を同じトランザクションで更新して守る (人数の判定に GSI を使わない)。
- 招待・停止・復帰・再招待・admin 有効化／解除と利用期間の更新では、書き込みと同じトランザクションで呼び出し元の行を確かめ、その間に停止・admin を外された呼び出し元は 403 `ADMIN_NOT_ACTIVE`。利用者の行は読んだ `version` を条件に書き、重なったら 409 `CONFLICT`。
- 再招待は新しい sub への付け替えまで `reinvite_in_progress` を立てる。その間と、古い sub の無効の印を確かめられない間は `restore` / `admin-activation` が 409 `REINVITE_INCOMPLETE`。通常の停止の admin は `restore` で戻す (TOTP が残っていること)。
- 通常の停止からの `restore` は、Cognito の無効化まで済んでいることが条件 (済んでいなければ 409 `SUSPEND_INCOMPLETE`。`suspend` を流し直すと Cognito の段だけを行う)。台帳が有効で Cognito が無効なら、`restore` は AdminEnableUser だけを行う。`restore` の順は Cognito の有効化 → 台帳 (有効化が失敗したら台帳を触らず 503 `TEMPORARILY_UNAVAILABLE`。有効化の後で台帳が失敗したら、Cognito は有効・台帳は停止のままで API は 403 になる。戻すには `suspend` を流し直してから `restore`)。
- email の変更は、台帳の途中の印 (`email_pending`) を書いてから Cognito を変え、印の操作 ID を条件に確定する。印がある間のほかの宛先への変更は 409 `CONFLICT` (同じ宛先なら続きから進む)。
- トランザクションごとの操作 ID を利用者の行の `last_op_id` に書き、失敗の後の読み直しでは「この操作が反映されたか」を操作 ID で確かめる。
- 一時パスワードの発行は監査の条件付き Put を先に書き、書けなければ 503 で Cognito を呼ばない。発行要求は detail.step=requested、Cognito 失敗時の result=failed は最善努力で追記する。
- 停止・再招待の理由は監査に 365 日残る。500 文字までで、暗証番号・連絡先・パスワードを書かない。
- 一時パスワードと連絡先は、ログ・監査・例外のメッセージに入れない。

利用期間 ID の参照行 (`DPERIOD#{id}` / `META`) を開始時に正本と同じトランザクションで作る。詳細と更新はこの行から DEVICE 配下の正本を強い整合で読み、GSI1 は一覧だけに使う。終了後も参照行は残す。

開始の valid_from / starts_at は省略・null を受け付けず、400 VALIDATION_ERROR。利用期間の API は日時を秒単位・タイムゾーン付き ISO 8601 で受け、UTC の `Z` 表記に揃える。期間は開始を含み終了を含まない。開始が前の期間の終了と同じなら隣接として受ける。null の終了は利用期間の終わりまで。利用期間の終了時に、開始が未来の割り当て・許可も同時刻で閉じる。過去に終了した行は保つ。

利用期間の API の主なエラー:

| HTTP | code | 条件 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 本文・一覧の status・日時・有効期間・service が不正 |
| 404 | `DEVICE_PERIOD_NOT_FOUND` / `ASSIGNMENT_NOT_FOUND` / `GRANT_NOT_FOUND` / `USER_NOT_FOUND` | 対象が存在しない |
| 409 | `DEVICE_PERIOD_ACTIVE` | 同じ通信機に CURRENT がある |
| 409 | `DEVICE_PERIOD_ENDED` / `ASSIGNMENT_ENDED` / `GRANT_ENDED` | すでに終了した対象への操作 |
| 409 | `ASSIGNMENT_OVERLAP` / `GRANT_OVERLAP` | 同じ利用者・サービスの有効期間が重なる |
| 409 | `CONFLICT` | 利用期間の version、終了する行、削除する CURRENT・LOGIN の条件が変わった |
| 409 | `TRANSACTION_LIMIT_EXCEEDED` | 一括処理が 100 操作を超える。書き込みは行わない |
| 500 / 503 | `LEDGER_WRITE_FAILED` / `OUTCOME_UNKNOWN` | 確定した失敗 / 再試行後も成否を確かめられない |

割り当て・許可の変更でも親の利用期間の `version` を更新する。一括終了の読み取りと競合した場合は全体を断る。作成・終了の監査は `billing=true`、TTL なし。サービスと期間・割り当ての ID だけを要約に使い、組織名と note の本文は監査やログに入れない。

### グループと access の API

グループと access (ADR D13 / D14、[API の設計](../../Documents/apps/admin-users/DESIGN.md)) の API。書き込みのまとまりと画面は [ユーザー管理 設計書](../../Documents/apps/admin-users/DESIGN.md) の「グループと access」。

| パス | メソッド | 内容 |
| --- | --- | --- |
| `/groups` | GET | グループの一覧。`status=active` (既定) / `all`。経路の順。応答 `data.groups[]` (`group_id`・`name`・`parent_group_id`・`path`・`depth`・`status`・`note`・作成と終了の日時と操作者) |
| `/groups` | POST | 作成。本文 `name` (1〜100 文字)・`parent_group_id` (省略で根)・`note` (500 文字まで)。段は 3 まで、段の名前は受けない (ほかの項目があれば 400)。201。応答はグループ (`path_names` つき) |
| `/groups/{group_id}` | GET | 詳細。一覧の項目に `path_names`・`children[]`・`members[]`・`assignments[]` を足したもの |
| `/groups/{group_id}` | PUT | 名前とメモの変更。本文 `name`・`note` (少なくとも 1 つ)。変わらなければ書かずに 200 |
| `/groups/{group_id}/end` | POST | 終える。稼働中の子・所属・終わっていない割り当てがあれば 409 `GROUP_IN_USE` |
| `/groups/{group_id}/members` | POST | 属させる。本文 `user_id`。所属ごとに新しい `membership_id` を振る (外して付け直せば別の値)。201 |
| `/groups/{group_id}/members/{user_id}/remove` | POST | 外す。ログイン記録は消さない (根拠が無くなり条件 (3) で落ちる) |
| `/device-periods/{id}/access` | PUT | 新設。本文 `access` (`everyone` / `assigned`)。稼働中だけ。同じ値なら書かずに 200。everyone にするたびに新しい世代 (`access_gen`。応答には出さない) を振る |

今のルートで変えるもの:

- `POST /device-periods`: 本文に `access` (省略で `everyone`)。利用期間の応答 (一覧・詳細・開始) に `access`・`access_changed_at`・`access_changed_by` (属性が無い行は `everyone`・`started_at`・`null`)。3 つは正本と稼働中の指し (`CURRENT`) の両方に書く (稼働中の一覧は `CURRENT` を読む)。
- `POST /device-periods/{id}/assignments`: 本文に `target_type` (`user` / `group`、省略で `user`) と `group_id`。`user` なら `user_id` が必須で `group_id` は不可、`group` なら逆。重なりは同じ相手の中で見る。詳細の `assignments[]` に `target_type`・`group_id`・`group_path_names`。
- `POST /device-periods/{id}/assignments/{assignment_id}/end` と `/end`: グループ宛の割り当ても閉じる。グループ宛と everyone の根拠のログイン記録は探して消さない。
- `GET /users/{user_id}`: `memberships[]` (`membership_id`・`group_id`・`path`・`path_names`・`added_at`)。
- 書き込みの守り: 上の書き込み (グループの作成・変更・終了、所属の追加・外す、グループ宛の割り当ての追加・終了、利用期間の開始の `access`、`access` の変更) は全部、今の admin の操作と同じく呼び出し元の検査 (ADR D9) を書き込みのトランザクションに入れる。所属の追加で対象が自分自身のときは、対象の存在の検査を呼び出し元の検査 1 つにまとめる。

足すエラー: 404 `GROUP_NOT_FOUND`・`MEMBER_NOT_FOUND`、409 `GROUP_ENDED`・`GROUP_DEPTH_EXCEEDED`・`GROUP_NAME_TAKEN`・`GROUP_IN_USE`・`MEMBER_EXISTS`。400 `VALIDATION_ERROR`、403 `ADMIN_NOT_ACTIVE` (書き込みの時点で呼び出し元が有効な admin でない)、409 `CONFLICT`・`ASSIGNMENT_OVERLAP` は今と同じ意味で使う。

足す監査: `group.create`・`group.update`・`group.end` (365 日)、`group.member_add`・`group.member_remove` (`target_user_id`、`detail.group_id`・`detail.membership_id`。`billing=true`)、`dperiod.access` (`detail.from`・`detail.to`。`billing=true`)。`assignment.add`・`assignment.end` の `detail` に `target_type` と `group_id`、`dperiod.start` の `detail` に `access`。グループの名前とメモは監査とログに入れない。

## 構成

| パス | 内容 |
| --- | --- |
| `template.yaml` | HTTP API / api Lambda / LogGroup / SSM `/apps/admin-users/api-domain`。台帳と監査は core のテーブル |
| `samconfig.toml` | `[default]`（dev）と `[production]`。prod は SSM の**キー名**を上書きする |
| `functions/admin_users/app.py` | ルートと呼び出し元の確認（`Handler: admin_users.app.handler`） |
| `functions/admin_users/device_periods.py`・`assignments.py`・`grants.py` | 利用期間・割り当て・許可の読み書きと一括終了 |
| `functions/admin_users/services.py` | 許可できるサービスの定義 |
| `functions/admin_users/users.py` | 操作 (招待・停止・再招待・有効化など) |
| `functions/admin_users/views.py` | 一覧と詳細の表示 |
| `functions/admin_users/email_change.py` | email の変更 (途中の印 → Cognito → 確定) |
| `functions/admin_users/tx.py` | 1 つのトランザクションの組み立てと、条件に落ちた項目の応答への写し |
| `functions/admin_users/ledger_writes.py` | 台帳と監査の行の組み立て (shared `transactions` で同じ token の再試行と読み直し) |
| `functions/admin_users/cognito.py` | Cognito 管理 API の薄いラッパ（Username は台帳の `cognito_username`） |
| `functions/admin_users/validation.py`・`errors.py`・`passwords.py` | 本文の検査、エラーのコード、一時パスワードの生成 |
| `tests/` | pytest + moto (台帳・監査・Cognito)。TOTP と AdminGetUserAuthFactors は薄い代理で足す |
| `edge-snippet.yaml` | `infra/edge/template.yaml` へ転記済みの origin / behavior / パラメータの写し |

**ハンドラのモジュール名は雛形の `api.app` ではなく `admin_users.app`。** 全アプリの
テストを 1 つの pytest セッションで走らせると `api` という同名パッケージが sys.modules で
衝突し、先に読まれた別アプリのモジュールを掴むため（ADD-APP-GUIDE「2. 雛形と SSM の受け渡し」）。

## テスト

```bash
uv run pytest apps/admin-users -q
uv run pytest apps/admin-users --cov=apps/admin-users/functions --cov-report=term-missing
```

## デプロイ

`sam build` を必ず先に実行する。

```bash
cd apps/admin-users
sam build --use-container && sam deploy                          # dev
sam build --use-container && sam deploy --config-env production  # prod
```

デプロイ順は shared → core → app → edge。デプロイすると
`/service-platform/{env}/apps/admin-users/api-domain` が SSM に書かれ、`scripts/deploy-edge.sh {env}` が経路を繋ぐ。

## 疎通確認

```bash
TOKEN=$(uv run python scripts/get_id_token.py dev {admin のメールアドレス})
curl -s "https://service-platform-dev.example-cloud.com/api/admin-users/users" -H "Authorization: Bearer $TOKEN"
```

有効な admin (TOTP を登録し、本人以外が有効にし、その後にパスワード + TOTP でサインインし直した) でなければ 403。

## 運用の設計要件

- 最初の admin と、使える admin がいないときの緊急の復旧は `scripts/admin_ops.py` (scripts/README.md)。
- 2 人目からは、有効な admin がこの API  で招待し、確認待ちになったら別経路で確かめて有効にする。
