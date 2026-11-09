# SERVICE-PLATFORM 運用手順書

更新日: 2026-11-06。公開範囲の基盤・本人 API の構築、運用、復旧を対象とする。[セキュリティ設計](SECURITY-DESIGN.md)、[ADR-001](ADR/ADR-001_認証と許可.md)、[スクリプトの説明](../scripts/README.md) を参照する。

## 作業前の確認

1. 対象環境、作業者、本人確認の別経路、影響する利用者を決める。同じ利用者への停止・復帰・再招待・email 変更を並行して行わない。緊急の復旧も担当を 1 人にする。
2. AWS CLI、SAM CLI、Docker、uv、Python、Node.js、npm、openssl を用意する。AWS_PROFILE と AWS_REGION で接続先を選び、SAM の設定も同じ環境にする。
3. コマンドはリポジトリのルートから実行する。下の例は dev。prod では SP_ENV=prod、SAM_ENV=production に替える。地域は通常 ap-northeast-1、CloudFront 用 WAF だけ us-east-1。

```bash
export SP_ENV=dev
export SAM_ENV=default
export AWS_REGION=ap-northeast-1
```

秘密・トークンを画面共有・ログ・引数に出さない。本文の email、理由、確認メモには実データを必要以上に含めない。テストメールと通知試験は、運用責任者が指定したテスト専用の宛先だけを使う。SP_OPS_EMAIL は下の手動購読で使うシェル変数であり、テンプレートや deploy-all.sh が自動では読まない。core はパラメータ OpsEmail を OpsTopic のインライン購読へ渡す。既存の購読先を確認し、ほかの宛先へ試験通知が届かないことを確かめる。実際の宛先の値は文書へ記載しない。

## 初回の構築：秘密と shared

1. `scripts/init-secrets.sh "$SP_ENV"` で origin-verify の秘密を登録する。登録済みなら変更しない。
2. Slack 通知を使う場合は `scripts/register-slack.sh "$SP_ENV" bot`（または webhook）で秘密を非表示入力する。方式は shared/samconfig.toml の SlackMode で選ぶ。`/shared/slack/mode` は手動で作らない。
3. shared を先に配備し、レイヤと SSM のキーを作る。

```bash
scripts/init-secrets.sh "$SP_ENV"
(
  cd shared
  sam build --use-container
  sam deploy --config-env "$SAM_ENV" --confirm-changeset
)
```

## 初回の構築：core と SES の切り替え

1. infra/core/samconfig.toml の環境を確かめる。SES Identity を検証する初回だけ COGNITO_DEFAULT で立てる。このとき EMAIL_OTP はテンプレートの条件で外れる。お客様の招待はまだ行わない。
2. SAM の `--parameter-overrides` は設定済みの一覧を置き換えるので、送信方式 1 個だけを渡さず、既存の一覧を保持する。次の例は samconfig を変更しない。

```bash
(
  cd infra/core
  sam build --use-container
  mapfile -t CORE_PARAMETERS < <(python3 - "$SAM_ENV" <<'PY'
import shlex
import sys
import tomllib

with open('samconfig.toml', 'rb') as source:
    parameters = tomllib.load(source)[sys.argv[1]]['deploy']['parameters']
values = shlex.split(parameters['parameter_overrides'])
values = [item for item in values if not item.startswith('CognitoEmailSendingAccount=')]
print('\n'.join([*values, 'CognitoEmailSendingAccount=COGNITO_DEFAULT']))
PY
  )
  test "${#CORE_PARAMETERS[@]}" -gt 1 || exit 1
  sam deploy --config-env "$SAM_ENV" --parameter-overrides "${CORE_PARAMETERS[@]}"
)
```

3. core が作る DKIM・MAIL FROM・DMARC の DNS と、SES Identity の検証成功を確認する。ドメインは core の MailDomain に合わせて SP_MAIL_DOMAIN に設定する。次で VerificationStatus が Success であること、送信可能であること、sandbox の状態を確かめる。

```bash
aws ses get-identity-verification-attributes --identities "$SP_MAIL_DOMAIN"
aws ses get-account-sending-enabled
aws sesv2 get-account --query '{ProductionAccessEnabled:ProductionAccessEnabled,SendingEnabled:SendingEnabled}'
```
4. core 作成直後に、次節の Ops トピックの購読を済ませる。
5. SES の検証成功後、上書きなしで core を再配備する。DEVELOPER と EMAIL_OTP が同時に有効になる。変更セットでユーザープールが置換されないことを確認する。論理 ID や RP ID を変更しない。

```bash
(
  cd infra/core
  sam build --use-container
  sam deploy --config-env "$SAM_ENV" --confirm-changeset
)
```

6. メールのコードの受信試験は、frontend と最初の admin の有効化後に「お客様の招待とパスキー」で行う。

## Ops トピックの購読

1. core の OpsTopic の購読一覧を確認する。インラインのメール購読も受信者の承認が必要。未承認のまま期限が切れた購読は、再配備だけで戻ると考えない。
2. 購読が無ければ承認済みの運用宛先を SP_OPS_EMAIL に設定して作る。既存の有効な購読があれば重複して作らない。

```bash
SP_OPS_TOPIC=$(aws ssm get-parameter \
  --name "/service-platform/$SP_ENV/ops/topic-arn" --query Parameter.Value --output text)
aws sns list-subscriptions-by-topic --topic-arn "$SP_OPS_TOPIC"
aws sns subscribe --topic-arn "$SP_OPS_TOPIC" --protocol email \
  --notification-endpoint "$SP_OPS_EMAIL"
```

3. 確認メールのリンクを期限内に開き、SubscriptionArn が PendingConfirmation でないことを一覧で確認する。確認は 3 日以内に行う。
4. dev のアラーム試験は指定のテスト用宛先への購読だけに通知されることを確認して行い、実際の受信まで確かめる。core の通知キューの DLQ アラームを利用する。試験後は次の評価で状態が戻ることを確認する。

```bash
aws cloudwatch set-alarm-state --alarm-name "service-platform-notify-dlq-$SP_ENV" \
  --state-value ALARM --state-reason smoke
aws cloudwatch describe-alarms --alarm-names "service-platform-notify-dlq-$SP_ENV" \
  --query 'MetricAlarms[].{State:StateValue,Actions:AlarmActions}'
```

## 初回の構築：waf・apps・edge・frontend

SES の検証と送信方式の切り替え、Ops の購読を済ませてから進める。

1. `scripts/deploy-waf.sh "$SP_ENV"` で CloudFront 用 web ACL と SSM を us-east-1 に配備する。
2. apps/admin-users、apps/base の各ディレクトリで `sam build --use-container`、`sam deploy --config-env "$SAM_ENV"` を実行する。
3. `scripts/deploy-edge.sh "$SP_ENV"` で API ドメイン・origin-verify・WAF を反映する。
4. `scripts/deploy-frontend.sh "$SP_ENV"` で SPA を配る。index.html のキャッシュ指定と CloudFront の無効化を確認する。
5. サイトの表示、本人 API、D9 を通る管理 API、両 WAF の関連付けと API の 401／403 を確認する。

2 回目以降は `scripts/deploy-all.sh "$SP_ENV"` を使える。shared → core → waf → apps → edge → frontend の順。初回の SES 検証・Ops の承認・最初の admin は本書の段階手順で行う。shared 更新後は参照アプリも再配備する。

## 最初の admin

1. IAM の権限を持つ運用者が別経路の本人確認を行う。ADMIN_EMAIL はその対象のメールアドレスに設定する。
2. 次を実行する。招待メールは送らず、一時パスワードはリポジトリ外の権限 600 のファイルに保存し、パスだけが出る。

```bash
scripts/add-admin.sh "$SP_ENV" "$ADMIN_EMAIL"
```

3. 一時パスワードを本人へ社内の別経路で渡す。本人は `/login/admin` で一時パスワード → 本パスワードと進み、`/account` で認証アプリの TOTP を登録する。パスワードは 12 文字以上、英大文字・英小文字・数字を含める。一時パスワードの期限は 3 日。
4. 登録完了 API が現在の TOTP を確かめ、既存セッションを失効させ、失効後の時刻を記録して awaiting にする。この段階では admin の操作は 403。
5. show-admin で PROFILE の admin_state=awaiting、mfa_enabled_at (JST)、Cognito の Enabled・UserStatus・UserMFASettingList を読む。運用者が別経路で本人と登録時刻を突き合わせ、確認方法を指定して有効にする。activate-admin も登録時刻と TOTP の状態を表示し、「本人に別経路で登録した時刻を確かめたか」を y/N で確認する。メモは必要な事実だけにする。

```bash
uv run python scripts/admin_ops.py show-admin "$SP_ENV" "$ADMIN_EMAIL"
uv run python scripts/admin_ops.py activate-admin "$SP_ENV" "$ADMIN_EMAIL" \
  --method phone --note '別経路で登録時刻と本人の操作を確認'
```

確認済みの非対話実行は activate-admin に --yes を付けられる。prod は --yes があっても確認を省略しない。

6. 本人がパスワード＋TOTP でサインインし直し、利用者の一覧を開けることを確かめる。auth_time は mfa_enabled_at より厳密に後である必要がある。ファイルを安全に受け渡したら不要な秘密ファイルを削除する。

作成時に結果不明で止まったら、秘密ファイルを残して「結果不明の確かめ方」の show-admin を使う。create-admin には再開用の状態ファイルが無い。作成済みなら作り直さず TOTP の段へ進む。Cognito だけ残った場合の削除は同節の条件を確かめてから行う。

## admin のパスキー

有効化後、本人がパスワード + TOTP で入り、`/account` の登録ボタンからパスキーを登録する。画面が続けて本人 API `/me/passkey-mfa` を呼び、TOTP を有効・優先のまま WebAuthn MFA を有効にする。成功後は通常の `/login` から本人確認つきのパスキーでも入れる。`/login/admin` のパスワード + 認証アプリも使える。準備中・確認待ちの admin は、先に TOTP の登録・別経路の確認・有効化を終える。

パスキーの登録より前に WebAuthn MFA を有効にすると Cognito が拒否する。利用設定だけ失敗したときは、画面の「利用設定をやり直す」で再試行する。追加直後の利用設定が失敗したときだけ sessionStorage に印を残す。次に画面を開いたとき、印があれば 1 回だけ利用設定を試し、成功で印を消す。失敗なら印と再試行ボタンを残す。409 PASSKEY_REQUIRED の場合は再試行ボタンを出さず、「先にパスキーを登録してください」と案内する。

この変更より前に有効になった admin も画面で登録すれば同じ手順で設定できる。IAM の権限を持つ運用者が設定する場合は、本人が先にパスキーを登録してから次を実行する。TOTP が現在有効で、台帳でも有効な admin だけを受け付ける。登録済みのパスキーも確かめ、無ければ理由を出して監査・MFA 更新を行わない。登録時刻と台帳の状態は変更せず、操作要求を監査してから Cognito を更新し、成功後に step=enabled を記録する。失敗した場合は理由を確認して再実行する。prod は確認を求める。

```bash
uv run python scripts/admin_ops.py enable-passkey-mfa "$SP_ENV" "$ADMIN_EMAIL"
```

core の更新時は、変更セットを実行する前に SignInUserPool の Modify / Replacement=False を確認する。

## admin を増やす

1. 有効な admin が `/admin-users` の招待を開き、種別「社員」、admin として招待を選ぶ。緊急時の連絡に備え、別経路の連絡先も入力する。既存の一般社員や admin を外した社員には「再招待」で役割 admin を選べる。
2. 応答の一時パスワードはダイアログで 1 回だけ表示される。メールやチャットに貼らず本人へ別経路で渡す。閉じると再表示できない。
3. 本人が本パスワード・TOTP を登録して確認待ちにする。
4. IAM の権限を持つ運用者は `uv run python scripts/admin_ops.py show-admin "$SP_ENV" "$ADMIN_EMAIL"` でも登録時刻と TOTP を確認できる。本人以外の admin が詳細画面で登録時刻を確認し、別経路で本人の操作と突き合わせ、「admin を有効にする」で方法・メモを記録する。
5. 本人がサインインし直し、操作できることを確認する。常に別の使える admin を確保する。一般社員は user として招待・再招待するとパスワード無しの利用者になる。

admin を外す操作だけでは、Cognito のパスワードと TOTP は残る。認証要素も完全に外すには、再招待で役割 user を選ぶ。旧 Cognito 利用者を削除してパスワード無しで作り直し、本人の新しいパスキーと別経路の確認後に有効に戻す。

## お客様の招待とパスキー

1. 有効な admin が招待で「お客様」を選び、email・組織名・別経路の連絡先を入力する。組織名は必須。メモは必要に応じて入力する。
2. frontend と最初の admin が使えるようになったら、指定のテスト専用宛先で試験用のお客様を 1 人招待し、メールのコードの受信とサインインを確認する。その後に通常の招待を始める。招待後に本人がメールのコードで入り、パスキーを登録する。自己登録の入口は無い。
3. 別の端末を足すときは、その端末でメールのコードなどにより入り、アカウント画面で追加する。端末ごとに登録しておくと紛失時に回復しやすい。
4. 単なる紛失で盗難の疑いが無ければ、別端末のパスキーかメールのコードで入り、不要なパスキーを削除して登録し直す。盗難・乗っ取りの疑いがあれば、次の停止・再招待の手順を使う。

admin でない社員の招待も同じ認証方式。パスキー管理は Cognito を直接呼ぶため、登録操作そのものは SERVICE-PLATFORM の監査表ではなく CloudTrail で追う。

## 通信機の利用期間・割り当て・サービスの許可

1. `/admin-users/device-periods` で「開始」。通信機アカウント（半角数字 8 桁）と組織名、必要ならメモを入力する。同じ通信機に稼働中の期間があれば新規開始できない。
2. 詳細で利用者を割り当てる。開始日時と任意の終了日時を指定する。停止中の利用者も登録できるが、利用停止はそのまま効く。admin が操作する通信機には admin 自身も割り当てる。
3. 詳細でサービスの許可を出す。登録済みサービスを選ぶ。開始・終了を指定する。割り当てと許可は別であり、片方だけではサービス利用の条件は揃わない。
4. 期間は開始を含み終了を含まない。終了 null は通信機の利用期間の終わりまで。同じ利用者の割り当て、同じサービスの許可は重ねない。画面は JST、API は秒単位・タイムゾーン付き日時を受け、UTC に揃える。
5. 利用を終えるときは、必要な割り当て・許可、または利用期間全体を終了する。未来開始の行も一括終了の対象。過去の終了済みの行は保つ。
6. 409 CONFLICT は詳細を読み直して内容を確認してから再実行する。TRANSACTION_LIMIT_EXCEEDED は書き込み無し。先に割り当て・許可を個別に終えてから期間を終える。

一括終了は CURRENT を消し、開いている割り当て・逆引き・許可を同時刻で閉じ、対象期間に属する有効な LOGIN を条件付きで消す。100 操作の上限と、親の version による同時更新の検査がある。終了済みの期間は再開せず、新しい期間を作る。

設置時に通信機の設定を読み取り、初回ログイン前の照合に使える状態にする。

利用者は `/devices` で、本人宛・グループ宛の割り当てと everyone の通信機を選ぶ。製造番号と秘密でログインし、許可されたサービスの一覧へ進む。前回値は通信機と通信せず照合し、最新値は前回値で秘密を確認してから読取りを要求する。

## 停止・有効に戻す・利用終了

1. 利用者の詳細で「停止」を行う。理由は admin の自由入力 (500 文字まで) で監査に 365 日残る。暗証番号・連絡先・パスワードを書かない。台帳が停止してから Cognito 無効化・全セッション失効へ進む。自分自身と最後の有効な admin は通常 API で停止できない。
2. 応答が失敗したら詳細を読み直す。台帳が停止・Cognito が有効なら停止を流し直して Cognito の段を完了する。SUSPEND_INCOMPLETE のまま復帰しない。
3. 通常の復帰は別経路で本人を確かめ、「有効に戻す」で方法を記録する。通常停止の admin は TOTP が残っていることも確認する。再招待後の admin は次節の有効化を使う。
4. 復帰の順は Cognito の有効化 → 台帳。途中で台帳更新が失敗すると Cognito 有効・台帳停止が残り、業務 API は拒む。この場合は停止を流し直してから復帰する。
6. 利用をやめる場合は停止に加え、関係するサービスの許可・割り当てを終える。記録は削除しない。

## 再招待と盗難への対処

パスキーの上限は 20 個。詳細画面に「パスワードでもサインインできます」と出たら、admin 以外にパスワードが足された疑いとして確認し、盗難・乗っ取りの疑いでは再招待を使う。

1. 盗難・乗っ取りの疑いを受けたら台帳を停止する。本人を別経路で確認し、再招待の理由を記録する。理由は admin の自由入力 (500 文字まで) で監査に 365 日残る。暗証番号・連絡先・パスワードを書かない。
2. 詳細で再招待後の役割 (user / admin) を選ぶ。省略時は現在の役割を引き継ぎ、admin にできるのは社員だけ。停止・無効化・全セッション失効・古い Cognito 利用者の削除・新規作成・sub の付け替えを行う。user_id と割り当ては残る。以前のパスキー・パスワード・TOTP は引き継がない。
3. お客様・一般社員は安全な受信箱でコードを受け、パスキーを登録し直す。台帳は停止のままで、本人はアカウントの画面だけを使う。
4. admin が本人の登録を別経路で確認して「有効に戻す」を実行する。パスキー未登録は PASSKEY_NOT_REGISTERED、付け替え未完了は REINVITE_INCOMPLETE。
5. admin として再招待するなら一時パスワードを別経路で渡し、本人が本パスワード・TOTP を登録して確認待ちにする。本人以外が「admin を有効にする」で台帳と admin の状態を同時に戻す。通常の restore では USE_ADMIN_ACTIVATION。
6. 再招待の結果不明時は詳細を再取得する。reinvite_in_progress が残る間は、同じ役割・宛先・理由でもう一度再招待すると続きから進む (admin なら一時パスワードは新しくなる)。付け替え完了後に再招待をもう一度押すと新たな再招待になるため、完了・途中を確かめてから実行する。

## 受信箱の乗っ取り・email の変更

1. まず停止し、問題の受信箱を本人確認の経路に使わない。
2. 台帳の別経路の連絡先で本人を確かめ、受信箱を安全に戻すか、安全な新しい宛先を決める。
3. 再招待で宛先と「先に本人を確認した」方法・メモを指定する。単に email を変えるだけでは、既存の認証要素を消す手順の代わりにならない。
4. 前節のパスキーまたは admin の TOTP 登録・別経路の確認・有効化まで進める。
5. 過去の直接操作は CloudTrail の sub を台帳の SUB 行から user_id へ引いて確認する。無効の印の行は消さない。

通常の email 変更でも別経路で申し出を確認する。途中状態があれば同じ宛先で再開し、完了後に台帳と Cognito の email の一致を確認する。

## admin の TOTP 紛失と緊急の復旧

使える別の admin がいれば、前節の再招待を使う。TOTP を解除するだけの回復は行わない。使える admin がいない場合は IAM の権限を持つ運用者が次を行う。

1. 社内の別経路で本人を確認し、復旧担当を 1 人にする。
2. 次の緊急再招待を実行し、表示された秘密ファイルと状態ファイルの置き場を控える。理由は監査に残る。暗証番号・連絡先・パスワードを書かない。

```bash
scripts/emergency-reinvite-admin.sh "$SP_ENV" "$ADMIN_EMAIL" \
  --reason '別経路で本人を確認して認証アプリ紛失から復旧'
```

3. 一時パスワードを別経路で渡す。本人が本パスワード・TOTP を登録し直して awaiting にする。
4. 運用者が `uv run python scripts/admin_ops.py show-admin "$SP_ENV" "$ADMIN_EMAIL"` で登録時刻 (JST) と TOTP を読み、本人の操作と別経路で突き合わせ、最初の admin と同じ activate-admin を行う。台帳も active に戻る。
5. 本人がパスワード＋TOTP で入り直し、使えることを確認する。監査と復旧記録を確認し、不要な秘密ファイルを削除する。

結果不明で止まった場合:

1. Cognito の利用者と秘密ファイルを削除しない。show-admin で status・admin_state・reinvite_in_progress・last_op_id と SUB の対応を確認する。成否未確定で削除すると、有効な台帳が存在する利用者を失い得る。
2. 表示された置き場を RECOVERY_DIR に設定し、同じ対象・理由・--output-dir で流し直す。

```bash
scripts/emergency-reinvite-admin.sh "$SP_ENV" "$ADMIN_EMAIL" \
  --reason '別経路で本人を確認して認証アプリ紛失から復旧' \
  --output-dir "$RECOVERY_DIR"
```

3. 状態ファイル `emergency-reinvite-{env}-{user_id}.json` は権限 600 で、一時パスワードを含まない。前の台帳更新が成功済みなら「前の回で済んでいました」と出て、前の秘密ファイルを使う。未反映なら保存した同じ token・同じ内容を送り直す。
4. Cognito と台帳の途中状態によっては新しい秘密ファイルが作られる。再実行の案内と状態ファイルが示す最新のファイルを使う。不一致が想定外なら処理は止まるので、手で状態を確認してから進める。
5. 確定した失敗かつ未反映のときだけスクリプトが補償の削除を行う。補償失敗も結果不明として扱う。状態ファイル整理の警告が出た場合は、台帳の成功と秘密ファイルを確認してから不要な状態ファイルを片付ける。

## 結果不明の確かめ方

次は読み取りだけで、prod でも確認は求めない。PROFILE の status・admin_state・mfa_enabled_at (JST)・reinvite_in_progress・last_op_id と、Cognito の Enabled・UserStatus・UserMFASettingList、今の Cognito の sub に対応する SUB 行の有無を表示する。秘密・contact・確認メモは出さない。

```bash
uv run python scripts/admin_ops.py show-admin "$SP_ENV" "$ADMIN_EMAIL"
```

PROFILE は強い整合で読み直すが、email の候補の検索は GSI1 の結果整合。見つからない場合は反映を待って再取得する。別担当の処理が動いていないことと、元のプロセスが終了し同じ token の再試行も終わっていることを確かめる。admin_state が awaiting なら登録時刻を突き合わせて有効化、active なら本人の再サインインを確認する。pending なら本パスワードと TOTP の登録から進める。

create-admin の後に Cognito が残り、繰り返し確認しても PROFILE が無く SUB が「対応なし」なら、台帳更新の未反映を確定する。IAM の権限を持つ運用者が、対象のメールアドレスを再確認して残った Cognito 利用者を削除し、create-admin をやり直す。削除前にもう一度 show-admin を行い、PROFILE または SUB があれば削除しない。要求がまだ処理中か確定できない場合も、削除せず調査を続ける。

```bash
SP_POOL_ID=$(aws ssm get-parameter --name "/service-platform/$SP_ENV/cognito/user-pool-id" \
  --query Parameter.Value --output text)
aws cognito-idp admin-delete-user --user-pool-id "$SP_POOL_ID" --username "$ADMIN_EMAIL"
scripts/add-admin.sh "$SP_ENV" "$ADMIN_EMAIL"
```

emergency-reinvite は Cognito の有無だけで削除や作り直しを決めず、前節の同じ --output-dir で再開する。last_op_id は状態ファイル内の PROFILE 更新の操作 ID と比較する。show-admin だけで処理完了と決めず、途中の印・状態ファイル・SUB の対応も合わせて判断する。

## admin のパスワード変更

1. 本人以外の有効な admin が対象を別経路で確認し、詳細で一時パスワードを発行する。
2. 一時表示を社内の別経路で本人へ渡す。閉じた応答の再表示はできない。受け渡せなかった場合は改めて発行する。
3. 本人が `/login/admin` で一時パスワード → NEW_PASSWORD_REQUIRED → 新しい本パスワードと進み、Cognito が求める TOTP に答える。TOTP を解除しない。

監査の条件付き Put が書けなければ 503 で発行を止める。発行要求の監査は detail.step=requested で、Cognito の成功証明ではない。Cognito が失敗した場合は result=failed の追記を最善努力で行う。応答を失った場合は状態を確かめ、必要なら再発行する。

4. 以前のパスワードで入れないことと、新しいパスワード＋TOTP で操作できることを確認する。ChangePassword は WAF で Block しているので使わない。一時パスワード発行は、全セッション失効や盗難時の再招待の代用ではない。

admin が 1 人だけのときは、自分への一時パスワード発行も ChangePassword も使えない。先に 2 人目を有効にして依頼するか、使える別の admin がいなければ IAM の運用者による緊急の再招待で回復する。

## WAF の設定と確認

公開用テンプレートのレート規則は Block。対象環境の通常利用、共有回線、招待・TOTP 登録・API 操作を踏まえて閾値を決める。変更は tests/infra の検査を含めてレビューし、ローカル検証後に反映する。変更後は利用者影響と遮断ログを確認する。

ユーザープールの ChangePassword 遮断は `uv run python scripts/admin_ops.py check-waf "$SP_ENV"` で確認する。実トークンによる試験には専用利用者を使い、アクセストークンを環境変数 SERVICE_PLATFORM_ACCESS_TOKEN に非表示で設定し、試験後に破棄する。

## WAF の ARN が無い場合の edge 配備

1. deploy-edge.sh は `/service-platform/{env}/waf/cloudfront-web-acl-arn` を必ず us-east-1 で読む。東京の同名キーでは代用しない。
2. 未登録・空値なら dev は ACL 無しで配備し、prod は停止する。まず `scripts/deploy-waf.sh "$SP_ENV"` を実行してから edge を流し直す。
3. prod で意図して ACL 無しにする例外時だけ、判断と復旧予定を記録したうえで `ALLOW_NO_WAF=1 scripts/deploy-edge.sh prod` を使う。通常の初回手順では使わない。
4. 権限不足・接続障害は両環境とも停止し、ALLOW_NO_WAF=1 でも通さない。原因を直して再実行する。
5. 配備後に Distribution の WebACLId と WAF の SSM の値が対応することを確認する。例外後は waf → edge で関連付けを戻す。次の 2 つの値を比較する。

```bash
SP_DISTRIBUTION_ID=$(aws cloudformation describe-stacks --stack-name "service-platform-edge-$SP_ENV" \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue | [0]" --output text)
aws cloudfront get-distribution-config --id "$SP_DISTRIBUTION_ID" \
  --query DistributionConfig.WebACLId --output text
aws ssm get-parameter --region us-east-1 \
  --name "/service-platform/$SP_ENV/waf/cloudfront-web-acl-arn" --query Parameter.Value --output text
```

## x-origin-verify のローテーション

1. CloudFront 更新中と Lambda の旧値キャッシュにより 403 が出る時間があるので、影響を案内して作業時間を決める。新旧 2 値を同時に受け付ける機能は無い。
2. 次を順に実行する。値は出力・コマンドライン・記録へ貼らない。

```bash
ROTATE=1 scripts/init-secrets.sh "$SP_ENV"
scripts/deploy-edge.sh "$SP_ENV"
```

3. CloudFront の更新完了を待つ。SSM の切り替えから Lambda のキャッシュ最長 300 秒も見込み、各 API の認証済み要求が通ることを確認する。
4. edge の配備が失敗したら、SSM をさらに変えず同じ deploy-edge.sh を再実行する。403 が続く場合は配備状況・接続先環境・SSM 読み取り権限を調べる。秘密の漏えいが疑われた場合もこの手順を使う。

## 対象環境への配備

1. 変更セット、復旧する版、利用者への影響、対象環境を確認する。prod は SP_ENV=prod／SAM_ENV=production、CloudFront 用 WAF は us-east-1。samconfig の SSM キー名を対象環境にそろえる。
2. 初回は秘密 → shared → core（COGNITO_DEFAULT）→ SES 検証・Ops 購読 → core（DEVELOPER／EMAIL_OTP）の段階で進める。ユーザープールとクライアントが置換されないことを確認する。
3. waf → admin-users → base → edge → frontend を反映する。レート規則・ChangePassword の遮断と WAF の関連付けを確認する。
4. 最初の admin は一時パスワード → TOTP → 本人以外の別経路確認 → 有効化 → 再サインインの順。登録時刻を show-admin で照合する。
5. 本人 API・管理 API・招待・コード・パスキー・通信機ログイン・サービス許可・監査を確認する。配備版・環境・検証結果を記録する。

失敗時は業務データを巻き戻さず、設定とコードの復旧を検討する。プール・台帳・監査を再作成しない。

## 配備前後の読み取り確認

対象環境の CloudFormation スタック、SSM のキー名、Cognito のプール・クライアント設定を読み取りで照合する。秘密は SecureString の型・版・存在だけを確認する。

認証は ESSENTIALS・招待制・USER_AUTH、rotation の猶予は 30 秒、RP ID は対象サイトのドメイン、UV は required。`WebAuthnFactorConfiguration=MULTI_FACTOR_WITH_USER_VERIFICATION` と、登録後の利用者ごとの `WebAuthnMfaSettings` も確認する。

CloudFront の WebACLId と us-east-1 の SSM の ARN を比較する。本人 API の 200、権限不足の 403、トークン無しの 401、直叩き時の ORIGIN_FORBIDDEN を確認する。SPA の index.html は CacheControl=no-cache とする。秘密やトークンをログ・記録に含めない。

## dev の E2E と片付け

frontend/e2e は dev 専用。scripts/e2e-users.py の create dev で example.com の利用者を SUPPRESS・恒久パスワードで作り、admin は TOTP 登録と activate-admin まで行う。秘密ディレクトリはリポジトリ外の Linux 側に置き、権限はディレクトリ 700、ファイル 600。--secrets-dir と E2E_SECRETS_DIR は必須で、既定値は無く、/mnt/ の下は拒否する。既存ディレクトリの権限は変更しない。作成時は保存の直後に権限を確かめ、不正なら Cognito の操作前に停止する。初回 PASSWORD セッションは Cognito から取得した実トークンを Amplify の保存形式で渡す。以降のパスキーと admin の認証は画面で行う。トレース・スクリーンショット・動画は保存しない。診断 JSON は test-results/ の各試験の出力へ保存し、レポートに添付する。

リポジトリのルートから次の 3 行を実行する。DIR は毎回新しく用意する上記の専用ディレクトリの絶対パスへ置き換え、3 行とも同じ値を指定する。

```bash
uv run python scripts/e2e-users.py create dev --secrets-dir DIR
(cd frontend && E2E_SECRETS_DIR=DIR npx playwright test)
uv run python scripts/e2e-users.py delete dev --secrets-dir DIR
```

途中で失敗しても削除する例。リポジトリのルートで次の全体を subshell のまま実行する。既存の dev 用 AWS セッションとインストール済みの依存を使う。

```bash
(
  set -e
  (umask 077; mkdir -p "$HOME/.local/state/service-platform-e2e")
  E2E_SECRETS_DIR=$(mktemp -d "$HOME/.local/state/service-platform-e2e/run-XXXXXX")
  export E2E_SECRETS_DIR
  export E2E_BASE_URL=https://service-platform-dev.example-cloud.com
  trap 'uv run python scripts/e2e-users.py delete dev --secrets-dir "$E2E_SECRETS_DIR"' EXIT
  uv run python scripts/e2e-users.py create dev --secrets-dir "$E2E_SECRETS_DIR"
  (cd frontend && npx playwright test)
)
```


E2E の利用者の作成と削除は監査しない (dev だけ、直後に消す)。

招待 API はメールを送るため、E2E では事前作成した利用者で停止・403・復帰を確認する。招待送信は指定のテスト宛先で確認する。通信機の試験には専用の利用期間と許可・割り当てを用意し、終了後に片付ける。

最後に同じ秘密ディレクトリを指定して scripts/e2e-users.py delete dev を実行する。作成済みの記録があれば、途中失敗でも delete で片付ける。稼働中の割り当てが残っている場合は有効な admin が専用利用者の割り当てを終了してから再実行する。終了した専用割り当ての正本・逆引き、Cognito、本人・SUB の行、有効な admin の集合から専用利用者を除く。監査は保持する。再試験は新しい秘密ディレクトリで create から行い、前回のセッションや仮想パスキーを再利用しない。

