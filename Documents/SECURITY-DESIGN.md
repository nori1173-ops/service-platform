# SERVICE-PLATFORM セキュリティ設計書

更新日: 2026-11-06。公開範囲の認証・認可・本人 API を記す。判断の根拠は [ADR-001](ADR/ADR-001_認証と許可.md)、操作手順は [RUNBOOK](RUNBOOK.md)。

## 認証の境界と流れ

専用の Cognito ユーザープールを使う。Essentials、招待制で、自己登録は許さない。PreSignUp も招待以外を拒む。社内 IP による制限は土台には置かない。

| 利用者 | 初回・回復 | 常用 |
| --- | --- | --- |
| お客様 | パスワード無しで招待し、メールのコード（EMAIL_OTP、8 桁、10 分以内）で入ってパスキーを登録 | メールアドレスを入力してパスキー。別端末の追加・紛失時はメールのコードも使える |
| admin でない社員 | 現実装はお客様と同じ。社員という種別だけではパスワードや TOTP を付けない | パスキーまたはメールのコード |
| 社員の admin | 招待メールを送らず一時パスワードを別経路で渡す。本パスワードを決め、TOTP を登録し、本人以外が登録を確認して有効にする | `/login/admin` のパスワード＋TOTP または `/login` の本人確認つきパスキー。有効化後に任意で登録 |

通常の入口は `/login`、本人の状態・登録は `/account`。USER_AUTH を使い、パスキーは WEB_AUTHN、admin のパスワードは PASSWORD_SRP を選ぶ。プールの MFA は OPTIONAL、要素は SOFTWARE_TOKEN_MFA。プールは `WebAuthnFactorConfiguration: MULTI_FACTOR_WITH_USER_VERIFICATION`、admin は `WebAuthnMfaSettings.Enabled=true` とし、本人確認つきのパスキーでも入れる。パスワードを選ぶと TOTP を求める。パスキーをパスワードの第二要素には使わない (ADR 判断 20)。TOTP の登録画面は admin にだけ出す。

パスキーは本人が Cognito を直接呼んで一覧・追加・削除する。複数登録でき、端末ごとに登録しておく。利用者が付ける名前は持たず、自動の種類名と登録日時で見分ける。RP ID は環境のサイトドメインに固定し、後から変えない。

パスワード方針は 12 文字以上、英大文字・英小文字・数字が必須。一時パスワードは 3 日間有効。本人による ForgotPassword は `admin_only` で止める。admin の変更は別の admin が一時パスワードを発行し、本人が NEW_PASSWORD_REQUIRED で決め直す。

email はスキーマで必須にせず、SPA クライアントの WriteAttributes は name だけ。email と email_verified を本人には書かせない。変更は admin が別経路の本人確認後に行い、Cognito の email_verified も設定する。

## トークンとブラウザ

ID・アクセストークンは 60 分、更新トークンは 24 時間。更新トークンの rotation は有効、再利用の猶予は 30 秒。更新しても最初のサインインからの期限や auth_time は延びない。ExplicitAuthFlows は ALLOW_USER_AUTH だけで、ALLOW_REFRESH_TOKEN_AUTH と ALLOW_USER_SRP_AUTH は使わない。ADR D1 で旧画面向けに一時的に残すとしていた ALLOW_USER_SRP_AUTH は、admin の画面も USER_AUTH の PASSWORD_SRP に揃えたため外している。

SPA は Amplify の既定の localStorage を使う。同じサイトの別タブもこの保存領域を使う。API の Authorization には ID トークンを付ける。HTTP API の JWT authorizer が署名・issuer・audience・期限を検査し、shared の auth が ID トークンのクレームを取り出す。SPA は 401 をサインアウトの合図にし、403 は停止・準備中・権限不足などの理由として扱う。

edge は HTTPS、HSTS、nosniff、フレーム埋め込み拒否、Referrer-Policy、CSP を設定する。CSP は script-src を self、connect-src を self と Cognito に絞る。Vuetify のインラインスタイルを許可し、スクリプトと通信先は必要な origin に限定する。

## 台帳による利用者の確定と停止

認可に使う読み取りは強い整合（ConsistentRead=True）。GSI は一覧表示だけに使う。

1. 検証済みトークンの sub から `SUB#{sub}` / `USER` を読む。行が無い、revoked_at がある、user_id が無い場合は 403 USER_NOT_REGISTERED。
2. `USER#{user_id}` / `PROFILE` を読み、現在の sub が一致することを確かめる。不一致も USER_NOT_REGISTERED。
3. status が active であることを確かめる。suspended または未知の状態は 403 USER_SUSPENDED。

停止中を許す例外は base の GET /me と POST /me/totp-registered のみ。例外でも古い sub の無効の印は迂回できない。再招待では user_id・割り当て・履歴を維持し、新しい sub を結び直す。古い SUB 行は消さず revoked_at を付け、CloudTrail の過去の sub からも追えるようにする。

停止は台帳を先に変更し、Cognito の無効化・全セッション失効へ進む。発行済みトークンが期限内でも、台帳を確かめる API は停止を拒む。Cognito と DynamoDB を一つの処理として確定できないため、途中の状態と再実行方法を RUNBOOK に定める。

## admin の守り（D9）

admin-users の全ルートは identity.require_user の後に admin_guard.require_admin を呼ぶ。次を全部満たす必要がある。

| 検査 | 条件 |
| --- | --- |
| クレーム | cognito:groups に admin がある |
| 台帳 | status=active、admin_state=active、mfa_enabled_at が存在する |
| セッション | auth_time が mfa_enabled_at より厳密に後。同じ秒・欠落・不正値は拒む。iat では比べない |
| Cognito の現在状態 | AdminGetUser で存在・Enabled・台帳と同じ sub・UserMFASettingList の SOFTWARE_TOKEN_MFA を確認し、AdminListGroupsForUser でも admin を確認 |

admin_state は pending（準備中）→ awaiting（確認待ち）→ active（有効）。本人が TOTP を登録した後、base が Cognito の現在の TOTP を確かめ、全セッションを失効させてから mfa_enabled_at と awaiting を書く。失効前のセッションを確実に ADMIN_SESSION_STALE にするため、失効後の時刻を記録する。台帳更新に失敗しても期限内の ID トークンで呼び直せる。有効化は本人以外が別経路で登録時刻を突き合わせて行う。最初の admin と緊急時は IAM の権限を持つ運用者が scripts の show-admin で JST の登録時刻と TOTP を読み、activate-admin の確認を経て行う。再招待は role を指定でき、admin は社員だけ。admin として作り直すと一時パスワード・admin グループ・pending、user として作り直すとパスワード無し・admin グループ無しとなる。admin 解除だけではパスワードと TOTP が Cognito に残るため、完全に外すには user として再招待する。

有効な admin には `/account` にパスキーの管理を表示し、登録後は `/login` からも入れる。準備中は TOTP の登録、確認待ちは有効化待ちの案内だけとする。既存の admin は `admin_ops.py enable-passkey-mfa` で有効化する。更新しても auth_time は変わらないため、登録前のセッションでは admin の操作は通らない。本人確認つきパスキーのサインインも登録後なら通る。WebAuthn MFA の有効化はパスキー登録後に `POST /me/passkey-mfa` で行う。既存の D9 と現在の TOTP を必須とし、要求の監査後に Cognito を更新する。パスキーが無ければ 409 PASSKEY_REQUIRED で登録を案内し、それ以外の失敗は 503 で、台帳の状態・登録時刻は変えず画面から再試行できる。TOTP の現在状態は ConfiguredUserAuthFactors では判定しない。acr / amr は採用版の PoC で使えず、現実装も使っていない（ADR 判断 12〜13）。

有効な admin の user_id の集合は `ADMIN#SET` / `ACTIVE` の members に保持する。有効化・通常の復帰で加え、停止・再招待開始・admin 解除で外す。通常 API の削除条件を台帳の変更と同じトランザクションに置き、最後の 1 人を外す場合は 409 LAST_ADMIN。自己の停止・復帰・再招待・有効化・一時パスワード・admin 解除は 403 SELF_OPERATION_FORBIDDEN。

招待・停止・復帰・再招待・admin 有効化／解除と利用期間の更新では、書き込み時も呼び出し元の台帳の active・admin_state・sub を検査する。プロフィール・email・一時パスワードも要求開始時に D9 を検査する。

## 通信機とサービスの条件

利用期間・割り当て・許可の管理、グループと access (ADR D13 / D14)、shared/access.py、base の通信機ログイン API は実装済み。admin も通信機を扱うときは根拠 (access が assigned の通信機では自分の割り当て) を必要とする。

| 条件 | 確かめるもの |
| --- | --- |
| (1) 稼働中の利用期間 P | `DEVICE#{account}` / `CURRENT` から正本の DPERIOD 行を読み、status=active |
| (2) P を扱う根拠 | (a) P の本人宛の割り当て (USER 行) が今有効、(b) P のグループ宛の割り当て (GROUP 行) が今有効で、そのグループが本人の所属の経路にある、(c) P の access が everyone (属性が無いときを含む) のどれか。有効は valid_from ≤ 現在時刻 < valid_to、終了 null は利用期間の終わりまで。access が assigned なら (a)(b) だけ |
| (3) 通信機ログイン記録 | 本人の LOGIN 行が P に属し、expires_at > 現在時刻 (ログインから 12 時間)、記録の根拠が今の (2) の根拠の集合に含まれる (user は同じ assignment_id、group は同じ assignment_id と membership_id の重なり、everyone は同じ access_gen)。時刻は比べない (ID と世代は使い回さない) |
| (4) サービスの許可 | P の GRANT 行が対象サービス・status=active で、starts_at ≤ 現在時刻 < ends_at |

(1)(2) の不足は DEVICE_NOT_ASSIGNED にまとめ、第三者へ稼働状態を知らせない。(3) は DEVICE_LOGIN_REQUIRED、(4) は SERVICE_NOT_GRANTED を返す。利用期間の終了・人宛の割り当ての終了は関連する LOGIN を条件付きで削除するが、TTL による後日の削除を認可の判定には使わない。access の切り替え・所属を外す・グループ宛の割り当ての終了では LOGIN を探して消さず、根拠の ID が今の集合から外れることで (3) で落とす。通信機ログインは、通信機ごとの失敗回数 (10 分に 5 回で 10 分) に加えて利用者ごとの失敗回数 (全通信機で 60 分に 10 回で 60 分、429 USER_PIN_LOCKED) で総当たりを抑える。グループ・所属・access の変更も、書き込みのトランザクションに呼び出し元の検査 (D9) を入れる。


未有効の admin は identity で ADMIN_NOT_ACTIVE。本人の状態参照と TOTP 登録完了だけを例外とする。

## WAF・直叩き・スロットリング

公開用テンプレートでは、ユーザープールと CloudFront に IP ごとのレート規則を置き、超過要求を遮断する。閾値は利用形態と共有回線を踏まえて環境ごとに決める。ユーザープールは ChangePassword を遮断し、管理者の初回設定・一時パスワードによる変更を別の導線で行う。HTTP API は既定 rate 20／burst 40、通信機ログインは rate 5／burst 10。

edge は HTTPS とセキュリティヘッダを設定する。CloudFront が付ける x-origin-verify と SSM SecureString を Lambda が定数時間で比較し、不在・不一致・取得失敗は 403 ORIGIN_FORBIDDEN。値は 300 秒キャッシュする。WAF・API・Lambda のログを対応付けて調査する。

## 監査と保持

core の別表 `service-platform-core-audit-{env}` に記録する。PK は `ACTOR#{user_id}`（scripts は `ACTOR#script`、無人処理は `ACTOR#system:{名前}`）、SK は UTC の発生時刻＋ULID。GSI1 は通信機、GSI2 は UTC の日付で引く。サービス・action・result（ok / denied / failed）・理由コード・対象 ID・要求元 IP／User-Agent・要約を持つ。

| 区分 | action と実装範囲 |
| --- | --- |
| 利用者（実装済み） | user.invite、user.profile_update、user.email_change、user.suspend、user.restore、user.reinvite、user.identity_verified |
| admin（実装済み） | admin.totp_registered、admin.activate、admin.temporary_password、admin.revoke |
| scripts（実装済み） | script.create_admin、script.activate_admin、script.emergency_reinvite |
| admin のパスキー MFA（実装済み） | admin.passkey_mfa_requested (Cognito 更新前)、admin.passkey_mfa_enabled (result=ok / failed、失敗時は code)。要求が書けなければ更新しない。結果の audit.emit は最善努力で、失敗しても業務を止めない |
| scripts のパスキー MFA（実装済み） | script.enable_passkey_mfa (detail.step=requested / enabled)。登録済みのパスキーを先に確かめ、無ければ監査・MFA 更新を行わない |
| 利用期間・許可（実装済み、billing=true） | dperiod.start、dperiod.end、assignment.add、assignment.end、grant.start、grant.end |
| 通信機（実装済み） | device.login、device.refresh_start、device.pin_locked |

監査への IAM は PutItem だけ。shared/audit.py が attribute_not_exists(PK) を付け、既存行を上書きさせない。台帳変更と監査は同じ TransactWriteItems に入れる。billing=true は TTL を付けず実績として残し、それ以外は発生から 365 日の TTL を付ける。利用期間の一括終了は関連行を閉じ、監査は dperiod.end を 1 件記録する。

一時パスワードの発行は AdminSetUserPassword の前に監査を条件付き Put し、書けなければ 503 で Cognito を呼ばない。この監査の detail.step=requested は発行要求の記録で、Cognito の成功を保証しない。Cognito の失敗時は result=failed の監査を audit.emit で最善努力で追記する。単独の audit.emit は失敗しても投げずログへ残すため、失敗記録の欠落や結果不明は CloudTrail と合わせて確かめる。API の拒否がすべて監査表に入るわけではない。構造化ログ、API アクセスログ、WAF ログ、既存の CloudTrail と合わせて追う。パスキーの直接の登録・削除は監査表を通らず、CloudTrail の sub と、残した SUB 行で対応を確認する（ADR 判断 16）。

台帳・監査はオンデマンド、保存時暗号化、dev も Retain。prod は PITR と削除保護を有効にする。Lambda・API・WAF のログは 365 日保持する。

## 秘密と個人情報の扱い

一時パスワードは scripts ならリポジトリ外の権限 600 のファイル、API なら成功応答で 1 回だけ渡す。画面は一時表示後にメモリから捨て、ストアや localStorage に残さない。メールやチャットに貼らず、本人へ別経路で渡す。台帳・監査・ログ・エラー・標準出力には出さない。

トークン・認証コード・TOTP の共有秘密・通信機の秘密・contact をログや監査へ入れない。audit は秘密らしい項目名を再帰的に除去するが、note や detail の本文の意味までは検査しない。呼び出し側が必要な ID・方法・件数に絞る。停止・再招待の理由は運用に必要なので例外として監査に 365 日残す。理由は admin の自由入力 (500 文字まで)。暗証番号・連絡先・パスワードを書かない。画面の理由欄にも同じ注意を出す。緊急の再招待も理由を監査に残す (スクリプトの入力上限は 200 文字)。確認メモは台帳にだけ残す。

招待リンクは `/login#email=` の宛先を補完してから URL から消す。SES のクリック追跡は使わない。EMAIL_OTP は 8 桁で、コードのメールにはリンクを入れない。メールアプリからの利用時は、同じ標準ブラウザで入力を続ける案内を追加した。


origin-verify や通知用の認証情報は SSM SecureString で管理する。deploy-edge.sh は権限 600 の一時 samconfig を使い、SAM の出力から秘密を伏せ、終了時に一時ファイルを消す。ローテーション時は CloudFront への反映と Lambda のキャッシュ待ちを含めて確認する。

