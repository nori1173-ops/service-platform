# 利用者・通信機の利用期間の管理設計書（admin-users）

更新日: 2026-11-06。利用者、グループ、通信機の利用期間・割り当て・サービス許可を記す。[セキュリティ設計](../../SECURITY-DESIGN.md)、[運用手順](../../RUNBOOK.md)、[ADR-001](../../ADR/ADR-001_認証と許可.md)、[アプリ README](../../../apps/admin-users/README.md) を併せて読む。

## 目的と構成

有効な admin が、利用者の招待・停止・復帰・再招待と、通信機の利用期間・割り当て・サービスの許可を管理する。本人の状態と TOTP 登録完了は base が持つ。自己登録、他人のパスキーの個別削除、TOTP 解除だけの回復、監査ログの閲覧画面は提供しない。

| 項目 | 内容 |
| --- | --- |
| スタック | service-platform-app-admin-users-{env} |
| API / Lambda | /api/admin-users、Handler: admin_users.app.handler、Python 3.13 |
| データ | core の台帳と監査。アプリ固有のテーブルは作らない |
| frontend | frontend/src/apps/admin-users/（Vue・Vuetify・Pinia） |
| edge | api-admin-users origin と /api/admin-users/* behavior |
| SSM | /service-platform/{env} 配下の /cognito/issuer、/cognito/client-id、/cognito/user-pool-id、/shared/layer-arn、/ledger/table-name、/audit/table-name を参照し、/apps/admin-users/api-domain を出力 |
| 防御と運用 | 全ルートに JWT・origin-verify・D9、既定 rate 20／burst 40、ログ 365 日。warmer は付けない |

## 利用者の識別と認証方式

user_id は shared/ids.py の new_ulid で招待時に採番する。ミリ秒の時刻 48 ビット＋乱数 80 ビットを Crockford Base32 の 26 文字にする。同じミリ秒内の順序は単調ではない。email や sub を user_id にせず、再招待でも同じ user_id を保つ。

Cognito の AdminCreateUser が返した Username を cognito_username、現在の sub を sub に保存する。管理 API の Username はこの控えを使い、email や sub の検索で毎回推定しない。email は小文字に揃える。

kind は customer / employee、role は user / admin。admin は employee としてだけ招待できる。一般の社員もパスワード無しで招待し、メールのコードとパスキーを使う。admin だけは招待メールを SUPPRESS し、一時パスワードを応答で 1 回だけ返す。role は表示・招待と再招待の控えで、権限の判定はトークンと Cognito の admin グループ、台帳の状態で行う。

## API：利用者

表のパスは /api/admin-users の後。成功応答は shared/response.py の共通エンベロープ。作成は 201、その他の成功は 200。/health を含め全ルートで identity.require_user → admin_guard.require_admin を行う。

| メソッド・パス | 本文・用途 |
| --- | --- |
| GET /health | data.status=ok。D9 を通る admin 専用 |
| GET /users | data.users と self_user_id。台帳の一覧と Cognito を合わせ、Cognito にだけいる人は in_ledger=false |
| POST /users | email、kind、role、org_name、contact、note。お客様の org_name は必須。admin の場合だけ temporary_password を返す |
| GET /users/{user_id} | 台帳と Cognito の状態、email、認証要素の有無、TOTP、登録・確認情報、割り当て |
| PUT /users/{user_id}/profile | org_name、contact、note |
| PUT /users/{user_id}/email | email。Cognito の email と email_verified を変更し、台帳へ確定 |
| POST /users/{user_id}/suspend | reason。台帳停止 → Cognito 無効化 → 全セッション失効 |
| POST /users/{user_id}/restore | verified_method（phone / in_person / video / other）、note。通常の復帰、または再招待後の一般利用者の復帰 |
| POST /users/{user_id}/reinvite | reason、任意の role (admin / user、省略時は現在の役割)、任意の email、任意の verified_before={method,note}。同じ user_id へ新しい sub を付け、台帳は停止を維持 |
| POST /users/{user_id}/admin-activation | method、note。本人以外が awaiting を active にする。再招待後は status も戻す |
| POST /users/{user_id}/temporary-password | 本人以外の admin が対象 admin の一時パスワードを発行。temporary_password を 1 回だけ返す |
| PUT /users/{user_id}/admin | admin=false のみ。台帳と有効な集合から外して Cognito のグループを外す |

admin=true は受け付けない。既存の一般社員や admin を外した社員は、再招待で role=admin を指定して admin にできる。admin としての再招待では Cognito の admin グループに付け、一時パスワードを返し admin_state=pending にする。role=user では admin グループを持たないパスワード無しの利用者を作り、ADMIN#SET からも外す。お客様を admin にする指定は 400。いずれも最後の admin の条件は維持する。

admin 解除だけでは、パスワードと TOTP が Cognito に残る。認証要素も完全に外すには role=user で再招待し、旧 Cognito 利用者を削除して作り直す。再招待後は本人の登録・別経路の確認を経て有効に戻す。

詳細の factors は password / web_authn / email_otp の有無。SDK が AdminGetUserAuthFactors を持たない場合は shared/cognito_extra.py が SigV4 で呼ぶ。TOTP の現在状態は AdminGetUser の UserMFASettingList を使い、ConfiguredUserAuthFactors で代用しない。

## API：通信機の利用期間

| メソッド・パス | 本文・用途 |
| --- | --- |
| GET /device-periods | status=active（既定）または all。data.device_periods |
| POST /device-periods | account（半角数字 8 桁）、org_name（1〜100 文字）、note（500 文字まで）。CURRENT と利用期間を作成 |
| GET /device-periods/{id} | 利用期間と assignments、grants |
| POST /device-periods/{id}/end | 開いている子行・関連 LOGIN を含めて一括終了 |
| POST /device-periods/{id}/assignments | user_id、valid_from、任意の valid_to。停止中の利用者・admin 自身も指定できる |
| POST /device-periods/{id}/assignments/{assignment_id}/end | 割り当てと逆引きを終了し、同じ割り当ての LOGIN を条件付きで削除 |
| POST /device-periods/{id}/grants | service=camera、starts_at、任意の ends_at |
| POST /device-periods/{id}/grants/{grant_id}/end | 許可を終了 |

日時は秒単位・タイムゾーン付き ISO 8601 を受けて UTC の Z 表記へ揃える。開始を含み終了を含まない。終了 null は利用期間の終わりまで。同じ利用者の割り当て、同じサービスの許可は期間の重なりを断り、前の終了と次の開始が同じ場合は受ける。利用期間自体の開始時刻はサーバで採り、割り当て・許可はそれより前に開始できない。

## グループと access

ADR D13 / D14 と [ADR-001](../../ADR/ADR-001_認証と許可.md) の設計を実装する。台帳の行は DESIGN 11.1 のグループ・所属・グループ宛の割り当て・製造番号の控え・利用者ごとの失敗回数の行。

### API：グループと所属

| メソッド・パス | 本文・用途 | 応答 (data) |
| --- | --- | --- |
| GET /groups | status=active (既定) / all。経路の順 (木の順) に並べる。GSI1 の `GROUP` から読む一覧 | groups[]: group_id、name、parent_group_id、path、depth、status、note、created_at / created_by、ended_at / ended_by |
| POST /groups | name (1〜100 文字)、parent_group_id (省略で根)、note (500 文字まで)。段の名前は受けない (ほかの項目があれば 400) | 201。group (上の項目と path_names) |
| GET /groups/{group_id} | 詳細 | 一覧の項目と path_names (根からの名前)、children[] (group_id、name、status)、members[] (membership_id、user_id、email、kind、org_name、status、added_at、added_by)、assignments[] (assignment_id、device_period_id、account、org_name、valid_from、valid_to、state) |
| PUT /groups/{group_id} | name、note (少なくとも 1 つ)。稼働中だけ。変わらなければ書かない | group (path_names つき) |
| POST /groups/{group_id}/end | 本文なし。稼働中の子・所属・終わっていない割り当てが無いときだけ | group (status=ended) |
| POST /groups/{group_id}/members | user_id。台帳に居る利用者 (停止中も可)。所属ごとに新しい membership_id を振る (外して付け直せば別の値) | 201。member (membership_id、user_id、group_id、path、added_at、added_by) |
| POST /groups/{group_id}/members/{user_id}/remove | 本文なし。ログイン記録は消さない (根拠が無くなり条件 (3) で落ちる) | member |

付け替え (親の変更) の API は作らない (ADR D13)。組み替えは新しいグループを作って所属と割り当てを移し、古いグループを終える。

### API：割り当ての相手と access

| メソッド・パス | 変えること |
| --- | --- |
| POST /device-periods | 本文に access (everyone / assigned、省略で everyone) を足す。正本と CURRENT に access・access_changed_at (開始の時刻)・access_changed_by (開始した人) を書き、everyone なら正本に新しい access_gen を振る。応答に access・access_changed_at・access_changed_by (access_gen は出さない) |
| GET /device-periods、GET /device-periods/{id} | 利用期間に access・access_changed_at・access_changed_by を返す。稼働中の一覧は CURRENT の写し (GSI1 の DEVICE)、status=all は正本 (GSI1 の DPERIOD)、詳細は正本 (強い整合) から返す。属性が無い行は access=everyone、access_changed_at=started_at、access_changed_by=null。詳細の assignments[] に target_type (user / group)・group_id・group_path_names を足す |
| PUT /device-periods/{id}/access | 新設。本文 access (everyone / assigned)。稼働中の利用期間だけ。同じ値なら書かずに 200。P の version を上げ、正本と CURRENT の access・access_changed_at・access_changed_by を書く。everyone にするときは正本に新しい access_gen を振る (前の everyone の記録を条件 (3) で落とすため。assigned にするときは access_gen を変えない)。応答は利用期間 |
| POST /device-periods/{id}/assignments | 本文に target_type (user / group、省略で user) と group_id を足す。user なら user_id が必須で group_id は不可、group なら group_id が必須で user_id は不可。重なりは同じ相手 (同じ利用者か同じグループ) の中で見る。応答に target_type・group_id |
| POST /device-periods/{id}/assignments/{assignment_id}/end | 人宛・グループ宛のどちらも受ける。グループ宛ではログイン記録を消さない |
| POST /device-periods/{id}/end | 開いているグループ宛の割り当てと逆引きも同じ時刻で閉じる。グループ宛と everyone の根拠のログイン記録は探して消さない (条件 (1) で落ちる) |
| GET /users/{user_id} | memberships[] (membership_id、group_id、path、path_names、added_at) を足す。assignments[] は本人宛のまま |

既存の人宛の割り当ての行は target_type を持たない。無いときは user と読み、書き換えない。

### 書き込みのまとまり (グループと access)

| 操作 | 同じトランザクションに入れるもの・条件 |
| --- | --- |
| グループの作成 | PROFILE (attribute_not_exists)、親の PROFILE (status=active、読んだ version で version を上げる) と親の下の CHILD、根なら GROUP#ROOT の version、監査 group.create、呼び出し元の検査。段と名前の重なりは、書く前に親の下 (根は GROUP#ROOT の下) の CHILD の控えを強い整合で読んで確かめ、親の version で競合を落とす |
| 名前・メモの変更 | PROFILE (status=active、version)、親の下 (根は GROUP#ROOT の下) の CHILD の name、親か GROUP#ROOT の version、監査 group.update、呼び出し元の検査。名前の重なりは作成と同じく CHILD の控えで確かめる |
| 終了 | PROFILE (読んだ version で status=ended)、親の下の CHILD の status、監査 group.end、呼び出し元の検査。書く前に GROUP#{group_id} を強い整合で Query し、稼働中の CHILD・MEMBER・終わっていない ASSIGN が無いことを確かめる。子・所属・割り当ての追加はどれもこの PROFILE の version を上げるので、読んだ後の追加とは競合で落ちる |
| 所属の追加 | 所属 (attribute_not_exists、新しい membership_id)、所属の逆引き (同じ membership_id)、グループの PROFILE (status=active、version を上げる)、利用者の PROFILE の ConditionCheck (attribute_exists)、監査 group.member_add、呼び出し元の検査。対象が呼び出し元自身なら、利用者の PROFILE の ConditionCheck を足さず、呼び出し元の検査 1 つにまとめる (1 つのトランザクションに同じ行への操作を 2 つ入れられない。呼び出し元の検査は行があることを含む) |
| 所属を外す | 所属と逆引きの削除 (attribute_exists、読んだ membership_id と同じ)、グループの PROFILE の version、監査 group.member_remove、呼び出し元の検査。読んだ後に外して付け直されていれば 409 CONFLICT |
| グループ宛の割り当ての追加 | 割り当て、逆引き (`GROUP#{group_id}` / `ASSIGN#...`)、グループの PROFILE (status=active、version を上げる)、利用期間の version、監査 assignment.add、呼び出し元の検査 |
| グループ宛の割り当ての終了 | 割り当てと逆引きの終了 (ログイン記録は消さない)、利用期間の version、監査 assignment.end、呼び出し元の検査 |
| 利用期間の開始 (access) | 今の項目 (CURRENT の不存在条件、正本、META、監査 dperiod.start、呼び出し元の検査) に、正本と CURRENT の access・access_changed_at・access_changed_by、everyone なら正本の access_gen を足す |
| access の変更 | 利用期間の正本 (status=active、読んだ version で access・access_changed_at・access_changed_by・version、everyone にするときは新しい access_gen)、CURRENT の access・access_changed_at・access_changed_by (device_period_id 一致)、監査 dperiod.access、呼び出し元の検査 |

呼び出し元の検査は今の admin の操作と同じもの (呼び出し元の `USER#{user_id}` / `PROFILE` の ConditionCheck。台帳の active・admin_state=active・同じ sub。ADR D9)。上の書き込みは全部、要求の初めの D9 に加えてこの検査を書き込みのトランザクションに入れ、落ちたら 403 `ADMIN_NOT_ACTIVE` で何も書かない (要求の後に止められた admin の変更を確定させない)。条件エラーの優先順位は今と同じ (LAST_ADMIN → ADMIN_NOT_ACTIVE → CONFLICT)。

CHILD の控えは状態と名前の写しを持つ。根の控えは GROUP#ROOT の下に置く (GSI1 は結果整合なので、名前の重なりの確かめに使わない)。

### 主なエラー (グループと access)

| HTTP | code | 条件 |
| --- | --- | --- |
| 400 | VALIDATION_ERROR | name・note・access・target_type、user_id と group_id の組み合わせが不正 |
| 403 | ADMIN_NOT_ACTIVE | 書き込みの時点で呼び出し元が有効な admin でない (今と同じ。上の書き込みの全部で確かめる) |
| 404 | GROUP_NOT_FOUND / MEMBER_NOT_FOUND | 対象が無い |
| 409 | GROUP_ENDED | 終了したグループ (親を含む) への操作 |
| 409 | GROUP_DEPTH_EXCEEDED | 親の段が 3 |
| 409 | GROUP_NAME_TAKEN | 同じ親の下 (根どうしを含む) に同じ名前の稼働中のグループがある |
| 409 | GROUP_IN_USE | 稼働中の子・所属・終わっていない割り当てがあるのに終えようとした |
| 409 | MEMBER_EXISTS | すでに属している |
| 409 | CONFLICT | グループ・親・利用期間の version が読んだ後に変わった、外す所属の membership_id が読んだ後に変わった (今と同じ意味) |
| 409 | ASSIGNMENT_OVERLAP | 同じ相手 (利用者かグループ) の有効期間が重なる (今と同じ) |

### 監査 (グループと access)

| action | 主な項目 | billing |
| --- | --- | --- |
| group.create | detail: group_id、parent_group_id、depth | false (365 日) |
| group.update | detail: group_id、changed (変えた項目の名前だけ) | false |
| group.end | detail: group_id | false |
| group.member_add / group.member_remove | target_user_id、detail: group_id、membership_id | true (割り当ての変更と同じく期限なし) |
| assignment.add / assignment.end | 今の項目に detail: target_type、group_id (グループ宛のとき。target_user_id は入れない) | true |
| dperiod.start | 今の項目に detail: access | true |
| dperiod.access | account、device_period_id、detail: from、to | true |

グループの名前とメモは、組織名と同じく本文として扱い、監査とログに入れない。

### 画面 (グループと access)

| パス | 表示・操作 |
| --- | --- |
| /admin-users/groups | グループの木 (3 段)、稼働中 / すべて。根の作成、子の作成、名前の変更、終了。作成のダイアログに段の名前の例 (会社 > 部署 > 課、県 > 事務所 > 出張所、コンサル > 支店) を出すだけで、段の名前は聞かない |
| /admin-users/groups/:id | 経路、子、所属 (追加・外す)、このグループ宛の割り当て (利用期間へのリンク) |
| /admin-users/device-periods | 一覧に access の列。開始のダイアログで access を選ぶ (既定 everyone) |
| /admin-users/device-periods/:id | access の表示と変更 (everyone と assigned の意味、assigned にすると割り当ての無い人が次の要求から入れなくなること)。割り当ての追加で相手 (人 / グループ) を選ぶ。割り当ての表に相手の型とグループの経路。引き渡しのチェックリストに「新しい暗証番号が効くまで、新しい利用期間は assigned で始める」 |
| /admin-users/users/:userId | 所属するグループの一覧 (グループの画面へのリンク) |

## 台帳の行

時刻は原則 UTC の文字列、mfa_enabled_at は epoch 秒。キーの組み立ては shared/ledger.py に集める。

| PK / SK | 用途・主要な属性 |
| --- | --- |
| USER#{user_id} / PROFILE | user_id、cognito_username、sub、email、status、kind、role、org_name、contact、note、created / updated の主体・時刻、version、last_op_id |
| SUB#{sub} / USER | user_id、created_at。古い sub は revoked_at を付けて残す |
| ADMIN#SET / ACTIVE | members（有効な admin の user_id の文字列集合） |
| DEVICE#{account} / CURRENT | device_period_id、org_name、started_at。同じ通信機で稼働中は 1 行 |
| DEVICE#{account} / DPERIOD#{id} | 利用期間の正本。status、開始・終了の主体と時刻、org_name、note、version、last_op_id |
| DPERIOD#{id} / META | account と device_period_id。API の ID から正本を強い整合で引く参照行。終了後も残す |
| DPERIOD#{id} / USER#{user_id}#{assignment_id} | account、user_id、valid_from / valid_to、role=user、登録・終了の主体、last_op_id |
| USER#{user_id} / ASSIGN#{id}#{assignment_id} | 割り当ての逆引き。account、利用期間・割り当ての ID、有効期間、org_name の写し |
| DPERIOD#{id} / GRANT#{service}#{grant_id} | account、service、starts_at / ends_at、status、登録・終了の主体、last_op_id |
| USER#{user_id} / LOGIN#{account} |  で作るログイン記録。ここでは終了時の条件付き削除を実装 |

グループ、所属、グループ宛の割り当て、製造番号の控え、利用者ごとの失敗回数の行と、属性 (access、target_type、basis) は DESIGN 11.1 と、上の「グループと access」。

PROFILE の admin 用項目は admin_state、mfa_enabled_at、admin_verified_by / at / method / note。停止・復帰・本人確認の主体と日時、restore_requires、previous_sub、reinvite_count、reinvite_in_progress、email_pending も操作の履歴・途中状態として保持する。

GSI1 の USER、DEVICE、DPERIOD は一覧用。利用者の確定や ID からの詳細・更新は正本を強い整合で読む。利用期間の META は GSI での逆引きを避けるために追加した。sub の引き当ては無効の印と PROFILE の現在 sub の一致を確かめる。

## トランザクションと同時更新

Tx が操作ごとの ULID を作り、書く PROFILE または利用期間の last_op_id に残す。shared/transactions.write は同じ token で再試行し、最後は強い整合で last_op_id を確認する。確定した未反映は WriteFailed、成否が確かめられない場合は OutcomeUnknown。Cognito 新規作成の補償削除は確定した失敗だけに行う。

| 操作 | 同じトランザクションに入れるもの・条件 |
| --- | --- |
| 招待 | PROFILE、新しい SUB、user.invite、呼び出し元の検査。既存行を上書きしない |
| プロフィール | 読んだ sub / version を条件に PROFILE と user.profile_update |
| 停止・通常復帰 | PROFILE と監査。対象が有効な admin なら ADMIN#SET の増減。呼び出し元の台帳も検査 |
| 再招待の開始 | status=suspended、restore_requires=reinvite、reinvite_in_progress、必要な ADMIN#SET からの除外、監査 |
| 再招待の付け替え | PROFILE の新 sub、新 SUB、古い SUB の revoked_at、監査。指定 role を保存。admin は pending、user は admin_state を消して ADMIN#SET からも外す。どちらも mfa_enabled_at と確認情報を消す |
| admin 有効化・解除 | PROFILE、ADMIN#SET、監査、呼び出し元の検査 |
| 利用期間開始 | CURRENT の不存在条件、正本、META、監査、呼び出し元の検査 |
| 割り当て・許可の追加・終了 | 子行、割り当ての逆引き、必要な LOGIN 削除、親の version 更新、監査、呼び出し元の検査 |
| 利用期間一括終了 | 正本の終了、期間 ID 一致を条件とする CURRENT 削除、開いている子行と逆引き、関連 LOGIN 削除、dperiod.end、呼び出し元の検査 |

PROFILE は sub と version の一致、利用期間は status=active と読んだ version の一致を条件にする。子の変更も親の version を進めるので、一括終了の Query の後で子が変われば全体を 409 CONFLICT にする。割り当ての重なりも親の version で競合を検出する。100 操作を超えれば書く前に TRANSACTION_LIMIT_EXCEEDED を返す。

一括終了は未来開始の子も終了時刻で閉じる。過去に終了した行は変えない。有効な LOGIN が対象期間・割り当てと一致するときだけ削除し、別の新しい記録は消さない。監査は dperiod.end が 1 件で、子ごとの終了 action を追加する作りではない。

各 API は要求開始時に D9 を検査する。グループと access の書き込み (グループ・所属・グループ宛の割り当て・access) は、台帳の active・admin_state=active・同じ sub の条件も書き込みの Tx に入れる (「書き込みのまとまり (グループと access)」)。

## 最後の admin と自己操作

ADMIN#SET の members から外す際、対象が集合に含まれるなら size が 1 より多いことを条件にする。有効な集合の変更と台帳更新を一体にし、GSI の人数や Cognito の一覧だけで判定しない。条件エラーの優先順位は LAST_ADMIN → 呼び出し元の ADMIN_NOT_ACTIVE → 対象の CONFLICT。

自分自身の停止・再招待・復帰・admin 有効化・解除・一時パスワード発行は SELF_OPERATION_FORBIDDEN。緊急時の scripts は通常 API の退路として IAM の権限を持つ運用者だけが使う。集合は台帳での有効状態を表し、全員の Cognito や端末が使えることまでは保証しない。

## 再招待・email・復帰の途中状態

reinvite_in_progress がある間、または古い SUB の無効化と新しい sub の対応を確認できない間は restore / admin-activation を REINVITE_INCOMPLETE で断る。新しい sub への付け替えが済んだら印を外すが、台帳は停止、restore_requires=reinvite のまま。一般利用者は新しいパスキーの登録と別経路の確認、admin は新しい TOTP の登録と本人以外の有効化を経て戻す。再招待の verified_before は method と note を受け、確認メモは台帳だけ、監査には方法などの要約を残す。

email は version を条件に email_pending={email,op_id} を書く → Cognito を更新 → 印の op_id を条件に email と GSI1SK を確定し印を消す。途中は別宛先を 409 で断り、同じ宛先なら続行する。確定段は印の op_id で直列化するため PROFILE の version の一致条件を使わない。

通常停止の復帰は Cognito 無効化済みが条件。復帰は Cognito 有効化 → 台帳の順で、途中失敗時は台帳の停止が残って API を閉じる。台帳有効・Cognito 無効なら restore は Cognito の有効化だけを行う。操作の流し直しは RUNBOOK に従う。

一時パスワードは AdminSetUserPassword（Permanent=false）の前に監査を条件付き Put する。監査に書けなければ 503 TEMPORARILY_UNAVAILABLE で Cognito を呼ばない。detail.step=requested は発行要求の記録で成功の証明ではない。Cognito が失敗したら result=failed の監査を audit.emit で最善努力で追記する。台帳更新とのトランザクションではない。全セッション失効や TOTP の付け替えはこの操作では行わない。

## 画面

| パス | 表示・操作 |
| --- | --- |
| /admin-users | email・台帳/Cognito の状態・種別・役割・登録日時、招待、台帳外利用者の表示 |
| /admin-users/users/:userId | 両者の状態・email、連絡先・メモ、認証要素・TOTP、登録/確認情報、割り当て。更新・停止・復帰・再招待・有効化・一時パスワード・admin 解除 |
| /admin-users/device-periods | 稼働中／すべての一覧、通信機の利用期間の開始 |
| /admin-users/device-periods/:id | 割り当てと許可の一覧・追加・終了、利用期間の一括終了と引き渡しのチェックリスト |

requiredGroups=['admin'] でメニューとルートを制御する。停止・準備中・確認待ちの人は土台のガードが /account へ案内する。どちらも表示の制御で、権限の実体は API。日時は JST で表示する。操作ダイアログを開いた時点の対象を保持し、遷移先の別利用者や別利用期間へ誤送信しない。

一時パスワードは OneTimeSecretDialog で一時表示するだけ。閉じたら捨て、Pinia・localStorage・ログ・エラーへ残さない。秘密のコピーは本人への別経路の受け渡しに使い、メールやチャットには貼らない。

## 主なエラー

| HTTP | code と意味 |
| --- | --- |
| 400 | VALIDATION_ERROR：本文・日時・サービスなどが不正 |
| 401 | UNAUTHORIZED：JWT のクレームが無いなど |
| 403 | ORIGIN_FORBIDDEN、USER_NOT_REGISTERED、USER_SUSPENDED、ADMIN_NOT_ACTIVE、ADMIN_SESSION_STALE、ADMIN_MFA_MISSING、SELF_OPERATION_FORBIDDEN |
| 404 | USER_NOT_FOUND、DEVICE_PERIOD_NOT_FOUND、ASSIGNMENT_NOT_FOUND、GRANT_NOT_FOUND |
| 409 | LAST_ADMIN、CONFLICT、REINVITE_INCOMPLETE、SUSPEND_INCOMPLETE、USE_ADMIN_ACTIVATION、PASSKEY_NOT_REGISTERED、ADMIN_NOT_AWAITING、ADMIN_NOT_READY、USER_EXISTS、USER_NOT_SUSPENDED、TARGET_NOT_ADMIN、COGNITO_USER_MISSING |
| 409 | DEVICE_PERIOD_ACTIVE、DEVICE_PERIOD_ENDED、ASSIGNMENT_ENDED、GRANT_ENDED、ASSIGNMENT_OVERLAP、GRANT_OVERLAP、TRANSACTION_LIMIT_EXCEEDED |
| 500 / 503 | LEDGER_WRITE_FAILED（確定した失敗）、OUTCOME_UNKNOWN（成否未確定）、TEMPORARILY_UNAVAILABLE（監査の必須書き込み・Cognito の有効化などが失敗） |

## IAM・監査・検証

対象プールの招待・属性更新・有効/無効・全セッション失効・削除・グループ・認証要素照会、一時パスワード設定の AdminSetUserPassword、一覧の ListUsers / ListUsersInGroup を許す。台帳と GSI1 の必要な読み書き・ConditionCheckItem、監査表の PutItem、origin-verify の取得に絞る。AdminResetUserPassword によるメール回復は使わない。

台帳と揃える監査の組み立ては shared/audit.py。利用期間・割り当て・許可は billing=true、TTL 無し。それ以外は通常 365 日。contact、秘密、確認メモの本文を監査へ含めない。停止・再招待の理由は運用に必要なので監査に 365 日残す。理由は admin の自由入力 (500 文字まで)。暗証番号・連絡先・パスワードを書かない。画面の停止・再招待の理由欄にも同じ注意を常時出す。Lambda の構造化ログと API アクセスログは 365 日保持する。拒否がすべて監査表へ入るわけではない。

apps/admin-users/tests は pytest + moto と Cognito の代理で状態遷移・競合・補償を確認する。frontend の __tests__ は表示、送信先の固定、期間、秘密の破棄を確認する。tests/infra が IAM・SSM・タグ・スロットリングを検査する。配備は shared → core → アプリ → edge → frontend、環境別の操作は RUNBOOK を参照する。

