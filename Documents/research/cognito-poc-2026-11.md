# Cognito の PoC の結果 (ADR-001 の 14 項目)

- 実施環境: ap-northeast-1 の独立した検証用スタック。稼働サービスのトラフィックや利用者データは使用しない。
- 対象: [ADR-001](../ADR/ADR-001_認証と許可.md) の PoC 14 項目。13 項目を条件付きのものも含め確認し、項目 6 の期待は否定された。
- 環境: 使い捨ての PoC スタック (`poc/cognito/template.yaml`。ユーザープール Essentials、`UsernameAttributes: [email]`、`AllowedFirstAuthFactors` = PASSWORD / EMAIL_OTP / WEB_AUTHN、WebAuthn の RP ID `localhost` と `UserVerification: required`、MFA OPTIONAL + TOTP、SES (DEVELOPER) の送信、`AccountRecoverySetting: admin_only`、`AllowAdminCreateUserOnly: true`、トークン 60 分 / 更新 24 時間)。アプリクライアントは 2 つ (rotation 無し: USER_AUTH / REFRESH_TOKEN_AUTH / USER_SRP_AUTH、rotation 有り: USER_AUTH だけ、猶予 0 秒)。ユーザープールに web ACL を 1 つ付けた。検証の後にスタックを消した。
- ブラウザ: headless の chromium で `http://localhost:8765` の静的ページを開き、aws-amplify 6.20.0 (土台と同じ版) と @aws-sdk/client-cognito-identity-provider 3.1161.0 を呼んだ。パスキーは CDP の仮想認証器 (ctap2、UV あり。PC 役は internal、スマホ役は hybrid)。実機の端末では試していない。
- 管理側: boto3 (1.42 系)。boto3 1.42 系には AdminGetUserAuthFactors が無いので、SigV4 で直接呼んだ。
- メール: 招待とメールのコードは、検証者が管理する専用のテスト宛先だけに送り、IMAP で受信を確かめた (5 秒ごとに確認したので、時間は 5 秒ほどの幅がある)。
- 利用者名がメールアドレスなので、テスト用の宛先の利用者は同時に 1 人しか作れない。前半はお客様役 (パスワード無しで招待)、後半は admin 役 (一時パスワードで作成、admin グループ) に入れ替えた。
- 判定の言葉: 「確認できた」= 期待どおりに動いた。「否定された」= 期待と違った。「確かめられなかった」= 試せなかった (理由を書く)。「推論」= 結果から導いたが、試してはいない。「手動観察」= 実行のときに別に読んだもので、ハーネスの記録 (`out/results.json`) には残らない。
- 再現: `poc/cognito/README.md`。項目ごとに `run_poc.py --only N` で流せる。

## 結果の一覧

| 番号 | 論点 | 判定 | 要点 | 決定への影響 |
| --- | --- | --- | --- | --- |
| 1 | email を利用者名にしたまま必須から外せるか | 確認できた | `UsernameAttributes: [email]` と `Schema` の email `Required: false` でプールを作れた。`AutoVerifiedAttributes: [email]`、`AttributesRequireVerificationBeforeUpdate: [email]` と同時に指定できた | D2 のとおり、email はスキーマの必須属性にせず、書込み属性を制限する |
| 2 | 利用者が UpdateUserAttributes で `email_verified` を書けないこと | 確認できた | WriteAttributes を `[name]` にしたクライアントで、本人のトークンの `email_verified=false` と `email` (同じ値) は NotAuthorizedException。`name` は通った | D2 のとおり。WriteAttributes を明示する |
| 3 | パスワード無しの利用者の ForgotPassword が `admin_only` で止まるか | 確認できた | NotAuthorizedException「Contact administrator to reset password.」。利用者の要素も変わらない。コードが送られないのは API の拒否からの推論で、受信箱は見ていない。存在しない利用者でも同じ応答 | D1 / D1 のとおり `admin_only` にする。`admin_only` でない設定は試していない |
| 4 | USER_AUTH で一時パスワードの利用者が NEW_PASSWORD_REQUIRED に進むか | 確認できた | Amplify の signIn (USER_AUTH、PASSWORD と PASSWORD_SRP のどちらも) で `CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED` になり、confirmSignIn で本パスワードを決めて CONFIRMED になった | D1 / D9 の admin の招待は USER_AUTH のままでよい |
| 5 | TOTP の利用者がパスキーとメールのコードで入れず、パスワード + TOTP で入れること | 確認できた | 候補 (SELECT_CHALLENGE) は PASSWORD と PASSWORD_SRP だけ。パスキーとメールのコードを求めても候補の選択に戻り、EMAIL_OTP を選ぶと InvalidParameterException。パスワード + TOTP は通った。TOTP の利用者もパスキーを登録でき、AdminGetUserAuthFactors には EMAIL_OTP と WEB_AUTHN が出る (使えないのに出る) | D9 のとおり。admin の要素を見るときは、ConfiguredUserAuthFactors ではなく MFA の設定を見る (11 も参照) |
| 6 | `acr` / `amr` が使えるか、`TARGET_ACR_VALUES` を渡せるか | 否定された (検証対象プール) | SDK の InitiateAuth に `TARGET_ACR_VALUES` を付けても受け付けるだけで、どのサインインのトークンにも `acr` / `amr` が出ない。存在しない段階や不正な `MAX_AGE` もエラーにならず、DescribeUserPool に `AcrConfiguration` が無い。公式の「段階的に提供中」のとおり、このプールにはまだ来ていないと見る (推論)。Amplify 6.20 はこの値を渡せない (ソース) | D9 は (b) 台帳の `mfa_enabled_at` で作る。(a) は提供が来たら試し直す。値は `loa:4` ではなく `urn:cognito:loa:4` (公式) |
| 7 | `/camera/` の別ビルドが土台のセッションを読めるか | 確認できた | 同じブラウザの新しいタブで、別のバンドルの Amplify が getCurrentUser と fetchAuthSession で同じ利用者 (同じ sub) のトークンを読めた | D6 のとおり (同じ origin、localStorage、同じクライアント) |
| 8 | AdminDeleteUser でパスキーも消えること | 確認できた | 削除して同じアドレスで作り直すと、要素は EMAIL_OTP だけ (前は PASSWORD / EMAIL_OTP / WEB_AUTHN)。前のパスキーでは候補に WEB_AUTHN が出ない。前のアクセストークンは UserNotFoundException | D8 手順 2 / 4 のとおり、パスキーと後付けのパスワードは消える |
| 9 | 招待とメールのコードが SES から届くこと | 確認できた | 招待もメールのコードも 6 秒ほどで受信箱に届いた (3 回。招待 1、コード 2)。パスワード無しの招待の本文は既定の英文で、利用者名だけ (一時パスワードは無い) | D1 のとおり。招待とコードのメールの文面 (既定は英語) を日本語にする作業が要る |
| 10 | 更新トークンの rotation を Amplify v6 で使えるか | 確認できた | Amplify 6.20 は更新に GetTokensFromRefreshToken を使う (ソース)。rotation 有りのクライアントで更新トークンが替わり、`auth_time` は変わらず、古い更新トークンは RefreshTokenReuseException。rotation 有りでは REFRESH_TOKEN_AUTH は UnsupportedOperationException。rotation 無しのクライアントの更新は、呼び出しがエラーなく返った (トークンの中身は未確認) | D1 の rotation を採れる。採るならクライアントの ExplicitAuthFlows から `ALLOW_REFRESH_TOKEN_AUTH` を外す。猶予 (RetryGracePeriodSeconds) は複数のタブを考えて決める (下の詳細) |
| 11 | AdminSetUserPassword (`Permanent=false`) の後に前のパスワードで入れないこと、TOTP を外した admin がメールのコードで入れるか | 確認できた (条件つき) | 前のパスワードは NotAuthorizedException。一時パスワードの間 (FORCE_CHANGE_PASSWORD) は TOTP を外しても候補はパスワードだけ。本パスワードを決めた後 (CONFIRMED) は、メールのコードでも、TOTP の間に登録したパスキーでも入れた | TOTP を解除した場合、本パスワードを決めて CONFIRMED になってから TOTP を登録し直すまでの間は、メールのコードとパスキーでも入れる。(b) で admin の API が 403 になるのは設計上の推論で、PoC では未検証。下の詳細に実装で確かめる条件 |
| 12 | パスキーを 2 つ以上登録でき、一覧で見分けられ、1 つ消しても残りで入れること | 確認できた (仮想認証器) | 2 つ登録でき、一覧の名前は「Device-bound Passkey」(platform) と「Roaming passkey」(cross-platform) だった。同じ認証器の 2 つ目はブラウザが断った。1 つ消すと一覧から消え、残りで入れ、消した方の assertion は Cognito が「Credential does not exist」で拒んだ | 名前は端末名ではなく種類の名前。PC とスマホをそれぞれの端末で登録すると、どちらも platform になり見分けにくい (推論)。SERVICE-PLATFORM 側で名前を持つかを詳細設計で決める |
| 13 | 本人のトークンで呼ぶ API が CloudTrail に残るか | 確認できた | 4 つとも記録され、`additionalEventData.sub` で利用者が分かる (ハーネスの記録)。書き込みの管理イベントであること、秘密値が隠されること、credentialId の有無は手動観察。WAF で止めた要求は、検索した時点と時間窓では検出しなかった | D8 / D12 の調べ方に使える。sub から user_id を引くため、再招待で変わる前の sub の対応を残す必要がある (ADR の D3 / D12 で、行を消さず無効の印を付ける) |
| 14 | パスワード無しの利用者がパスワードを足せるか、web ACL で止められるか | 確認できた | SDK の ChangePassword (`PreviousPassword` 無し) で足せ、AdminGetUserAuthFactors に PASSWORD が出て、PASSWORD と PASSWORD_SRP でサインインできた (Amplify の updatePassword は旧パスワードが空だと手前で断る)。web ACL のルール (ヘッダ `x-amz-target` が ChangePassword) を Block にすると 403 で止まり、ほかの公開 API、パスキーのサインイン、admin の一時パスワードの変更と TOTP の登録、AdminSetUserPassword は通った | D1 で採用した WAF の Block を適用できる。止めても admin の運用 (一時パスワード → 本パスワード、TOTP の登録) は通る。止まるのは本人の ChangePassword だけ |

## 項目ごとの詳細

### 1. email を利用者名にしたまま、スキーマの必須から外せるか (D2)

- 期待: `UsernameAttributes: [email]` のまま、`Schema` の email を `Required: false` にしてプールを作れる。
- やったこと: CloudFormation で上の設定のプールを作り、DescribeUserPool を読んだ。
- 結果: スタックの作成が通り、DescribeUserPool の email は `Required: false`、`Mutable: true`。`UsernameAttributes` は `[email]`、`AutoVerifiedAttributes` は `[email]`。`AttributesRequireVerificationBeforeUpdate: [email]` も同時に受け付けた。この設定で、email と `email_verified=true` を渡す AdminCreateUser も通った (9、8)。
- 判定: 確認できた。

### 2. 利用者が UpdateUserAttributes で `email_verified` を書けないこと (D2)

- 期待: WriteAttributes に `email` と `email_verified` を入れないクライアントでは、本人が書けない。
- やったこと: WriteAttributes を `[name]` にしたクライアントでお客様役がサインインし、本人のアクセストークンで UpdateUserAttributes を呼んだ (SDK と Amplify)。
- 結果:
  - `email_verified=false`: NotAuthorizedException「A client attempted to write unauthorized attribute」(SDK、Amplify とも)。
  - `email` (同じ値): 同じ NotAuthorizedException。
  - `name` (比較のため): 通った。
  - 後で読むと `email_verified` は true のまま。
- 判定: 確認できた。
- 補足: WriteAttributes を空にしたときの扱いは試していない。必ず書いてよい属性を明示する。

### 3. パスワード無しの利用者が ForgotPassword でパスワードを得られないこと (D1 / D1)

- 期待: `AccountRecoverySetting: admin_only` のプールで、パスワード無しの利用者の ForgotPassword はエラーになる。
- やったこと: パスワード無しのお客様役 (要素は EMAIL_OTP だけ) に ForgotPassword を呼んだ。比べるため、存在しない利用者でも呼んだ (`PreventUserExistenceErrors: ENABLED`)。
- 結果: どちらも NotAuthorizedException「Contact administrator to reset password.」。呼んだ後の要素は EMAIL_OTP だけ、状態も CONFIRMED のまま。存在の有無で応答は変わらない。メールが送られないことは API の拒否からの推論で、受信箱の監視はしていない。
- 判定: 確認できた。`admin_only` でないとき (`verified_email` など) に、パスワード無しの利用者が ForgotPassword でパスワードを得られるかは試していない (採る設定ではないため)。

### 4. USER_AUTH で一時パスワードの利用者が NEW_PASSWORD_REQUIRED に進むか (D1 / D9)

- 期待: Amplify の signIn (USER_AUTH、`preferredChallenge` PASSWORD / PASSWORD_SRP) で `CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED` になる。
- やったこと: admin 役を一時パスワード・`MessageAction=SUPPRESS` で作り (状態 FORCE_CHANGE_PASSWORD)、PASSWORD で signIn → confirmSignIn。続けて AdminSetUserPassword (`Permanent=false`) で一時パスワードに戻し、PASSWORD_SRP でも同じことをした。
- 結果: どちらも `CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED` (`missingAttributes` は空) → confirmSignIn で `DONE`、状態は CONFIRMED。
- 判定: 確認できた。

### 5. TOTP を設定した利用者は、パスキーとメールのコードでサインインできないこと (D9)

- 期待: プールの MFA が OPTIONAL のとき、TOTP を設定した利用者は、パスキーとメールのコードでは入れず、パスワード + TOTP で入れる。
- やったこと: admin 役がパスワードで入り、TOTP を登録した (Amplify の setUpTOTP → verifyTOTPSetup → updateMFAPreference で PREFERRED)。パスワード + TOTP で入り直し、そのセッションでパスキーも登録した。その後、USER_AUTH の候補、パスキー、メールのコードを試した。
- 結果:
  - パスワード + TOTP: `CONFIRM_SIGN_IN_WITH_TOTP_CODE` → `DONE`。
  - TOTP のセッションでのパスキーの登録: 通った。AdminGetUserAuthFactors の ConfiguredUserAuthFactors は PASSWORD / EMAIL_OTP / WEB_AUTHN / SOFTWARE_TOKEN。
  - USER_AUTH の候補 (USERNAME だけで InitiateAuth): SELECT_CHALLENGE で PASSWORD と PASSWORD_SRP だけ。
  - Amplify でパスキー (`preferredChallenge: WEB_AUTHN`) とメールのコード (`EMAIL_OTP`) を求めると、どちらも `CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION` (候補は PASSWORD と PASSWORD_SRP)。メールが送られないことは応答からの推論で、受信箱の監視はしていない。
  - SELECT_CHALLENGE に EMAIL_OTP を答えると InvalidParameterException「Password Challenge is Required to SignIn.」。
- 判定: 確認できた。
- 補足: ConfiguredUserAuthFactors には、使えない EMAIL_OTP と WEB_AUTHN も出る。admin の状態の確認には、AdminGetUser の UserMFASettingList (または AdminGetUserAuthFactors の UserMFASettingList) を使う。

### 6. `acr` / `amr` と `TARGET_ACR_VALUES` (D9)

- 期待: ID トークンに `acr` / `amr` が付き、USER_AUTH に `TARGET_ACR_VALUES` を渡せる (Amplify か SDK)。
- やったこと:
  - SDK (@aws-sdk/client-cognito-identity-provider) の InitiateAuth (USER_AUTH、PASSWORD) に `TARGET_ACR_VALUES` を `urn:cognito:loa:4` と `loa:4` で付け、TOTP に答えてトークンを得た。GetTokensFromRefreshToken で更新した後のトークンも見た。
  - 存在しない段階 (`urn:example:no-such-level`) と、数でない `MAX_AGE` でも呼んだ。
  - DescribeUserPool の生の応答 (SDK の型を通さない) に `AcrConfiguration` があるかを見た。
  - 他の項目のトークン (メールのコード、パスキー、パスワード、パスワード + TOTP) のクレームも並べた。
  - aws-amplify 6.20.0 のソース (`handleUserAuthFlow`) を読んだ。
- 結果:
  - どのサインインでも、ID トークンとアクセストークンに `acr` と `amr` が無い (更新後も無い)。`auth_time` はある。
  - `TARGET_ACR_VALUES` を付けても応答は普段と同じ (SOFTWARE_TOKEN_MFA の要求)。存在しない段階も不正な `MAX_AGE` もエラーにならない。Essentials なので FeatureUnavailableInTierException も出ない。
  - DescribeUserPool に `AcrConfiguration` が無い。
  - Amplify 6.20 は AuthParameters に USERNAME / PASSWORD / SRP_A / PREFERRED_CHALLENGE だけを入れ、`TARGET_ACR_VALUES` を渡す手段が無い。
- 判定: 否定された (検証対象プール)。公式の文書は「段階的に提供中で、まだ使えないプールがある」と書いており、パラメータを黙って無視することから、このプールにはまだ来ていないと見る (推論)。
- 影響: D9 のセッションの確認は (b) 台帳の `mfa_enabled_at` と `auth_time` で作る。(a) は、提供が来たら試し直す。(a) にするなら、Amplify ではなく SDK を直接呼ぶ admin のサインインが要る (Amplify 6.20 の時点)。ADR の `loa:4` は、既定の値では `urn:cognito:loa:4` (公式。`AcrConfiguration` で名前を変えられる)。

### 7. `/camera/` の別ビルドが、土台の SPA のセッションを読めるか (D6)

- 期待: 同じ origin で別にバンドルした `/camera/` が、同じプールとクライアントで Amplify を初期化すれば、土台が localStorage に保存したセッションを読める。新しいタブでも読める。
- やったこと: `/` でお客様役がサインインした後、同じブラウザの新しいタブで `/camera/` (別の esbuild のバンドル) を開き、getCurrentUser と fetchAuthSession を呼んだ。
- 結果: 利用者 (sub) が読め、ID トークンとアクセストークンがあり、sub は `/` と同じ。localStorage の Amplify のキーは両方で 6 個。
- 判定: 確認できた。
- 補足: 静的ページは localhost の簡単なサーバーで出した。CloudFront の behavior とフォールバックは試していない (CloudFront の結合試験)。

### 8. AdminDeleteUser でパスキーも消えること (D8)

- 期待: AdminDeleteUser → 同じメールアドレスで AdminCreateUser にすると、前のパスキーと後付けのパスワードは新しい利用者に残らない。
- やったこと: パスキー (スマホ役) と後付けのパスワード (14) を持つお客様役を AdminDeleteUser し、同じアドレスで作り直した (パスワード無し、`SUPPRESS`)。要素、前のアクセストークン、前のパスキーでのサインインを確かめた。
- 結果:
  - 要素: 削除の前は PASSWORD / EMAIL_OTP / WEB_AUTHN、作り直した後は EMAIL_OTP だけ。
  - 前のパスキー: Amplify のパスキーのサインインは `CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION` で、候補は EMAIL_OTP だけ。
  - 前のアクセストークン (GetUserAuthFactors): UserNotFoundException。同じ利用者名の利用者がいても、前のトークンは使えない。
- 判定: 確認できた。

### 9. 招待とメールのコードが SES から届くこと (D1)

- 期待: SES (DEVELOPER) から、招待メールとメールのコードが届く。
- やったこと: AdminCreateUser (一時パスワード無し、`DesiredDeliveryMediums: EMAIL`、`email_verified=true`) で招待し、続けて USER_AUTH の EMAIL_OTP でサインインした。受信は IMAP で 5 秒ごとに確かめた。送信元は検証済みの SES ドメイン。
- 結果:
  - 招待: 約 6 秒で届いた。件名「Your username」、本文「Your username is (メールアドレス)」。パスワード無しの利用者なので一時パスワードは入っていない (既定のテンプレート)。
  - メールのコード: 約 6 秒で届いた (別の回でも約 6 秒)。件名「Your authentication code」、本文「Your authentication code is (コード)」。コードを答えると `DONE`、`email_verified` は true。
  - どれも受信箱に届いた (迷惑メールの振り分けは無かった)。
- 判定: 確認できた。
- 影響: 文面は既定の英文。招待のメッセージ (`InviteMessageTemplate`) と、メールのコードの文面を日本語にする手段を詳細設計で決める (コードの文面の変え方は今回調べていない)。

### 10. 更新トークンの rotation を Amplify v6 で使えるか (D1 / D1)

- 期待: Amplify v6 (6.20) が GetTokensFromRefreshToken で更新し、rotation を有効にしたクライアントで更新トークンが替わる。
- やったこと: Amplify 6.20.0 のソース (`refreshAuthTokens`) を読んだ。rotation 有り (猶予 0 秒、ExplicitAuthFlows は USER_AUTH だけ) のクライアントでお客様役がパスキーで入り、`fetchAuthSession({ forceRefresh: true })` を 2 回呼んだ。古い更新トークンの再利用と、rotation 有りのクライアントでの REFRESH_TOKEN_AUTH も試した。rotation 無しのクライアントでも更新した。
- 結果:
  - Amplify 6.20 は更新に必ず GetTokensFromRefreshToken を使い、応答に新しい更新トークンがあれば保存し直す。
  - rotation 有り: 更新が通り、localStorage の更新トークンが替わった。アクセストークンの `iat` は新しくなり、`auth_time` は変わらない。2 回目の更新は、呼び出しがエラーなく返った (ハーネスは返り値を捨てるので、トークンの中身は未確認)。
  - 古い更新トークンを再び使う: RefreshTokenReuseException「Refresh token reuse detected」。
  - rotation 有りのクライアントで REFRESH_TOKEN_AUTH: UnsupportedOperationException「This API does not support refresh token rotation」。
  - rotation 無し: 更新の呼び出しがエラーなく返り、保存された更新トークンは替わらなかった (ハーネスは返り値を捨てるので、更新後のトークンの中身は未確認)。
- 判定: 確認できた。rotation の本体 (更新トークンが替わる、古いものは RefreshTokenReuseException、`auth_time` は変わらない) は、更新後のトークンを読んで確かめた。
- 影響: D1 で rotation を採れる。採るなら、アプリクライアントの ExplicitAuthFlows から `ALLOW_REFRESH_TOKEN_AUTH` を外す (rotation 有りでは REFRESH_TOKEN_AUTH が使えない)。猶予 0 秒では、同じ localStorage を共有するタブや `/camera/` が同時に更新すると、遅れた方が再利用と判定されうる (推論。試していない)。D1 では猶予 30 秒を採用する。同時更新は追加検証の対象とする。

### 11. AdminSetUserPassword の後の前のパスワード、TOTP を外した admin (D9)

- 期待: AdminSetUserPassword (`Permanent=false`) の後は、前のパスワードで入れない。TOTP を外した admin がメールのコードで入れるかを見る (入れるなら、解除の間の入口になる)。
- やったこと: TOTP を設定した admin 役に AdminSetUserPassword (一時パスワード、`Permanent=false`) → 前のパスワードでサインイン。AdminSetUserMFAPreference で TOTP を外し、USER_AUTH の候補とメールのコードを試した。続けて、一時パスワードから本パスワードを決め (CONFIRMED)、TOTP を外したまま候補、メールのコード、パスキー (5 で TOTP の間に登録したもの) を試した。
- 結果:
  - 前のパスワード: NotAuthorizedException「Incorrect username or password.」。状態は FORCE_CHANGE_PASSWORD。
  - TOTP を外した直後 (FORCE_CHANGE_PASSWORD の間): 候補は PASSWORD_SRP と PASSWORD だけ。メールのコードを求めても候補の選択に戻る。一時パスワードで入ると `CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED`。
  - 本パスワードを決めた後 (CONFIRMED、TOTP は外したまま): 候補は PASSWORD_SRP / PASSWORD / EMAIL_OTP / WEB_AUTHN。メールのコードで入れた (約 6 秒で届いた)。TOTP の間に登録したパスキーでも入れた。
  - TOTP を外した後も、ConfiguredUserAuthFactors には SOFTWARE_TOKEN が残る (UserMFASettingList は空)。
- 判定: 確認できた (前のパスワードでは入れない。TOTP を外した admin は、一時パスワードの間はパスワードだけ、本パスワードを決めた後はメールのコードとパスキーでも入れる)。
- 影響 (D9):
  - 「TOTP を解除する」の後、本パスワードを決めて CONFIRMED になってから本人が TOTP を登録し直すまでの間は、メールのコードとパスキーでもサインインできる (FORCE_CHANGE_PASSWORD の間はパスワードだけ)。
  - (b) なら、その間に得たセッションの `auth_time` は新しい `mfa_enabled_at` より前なので admin の API は 403 になる。これは設計上の推論で、台帳と API を持たない PoC では未検証。実装で、admin の状態、現在の MFA (UserMFASettingList)、`auth_time` と新しい `mfa_enabled_at` の比較を確かめる。
  - admin が TOTP の間に (本人か、セッションを得た者が) 登録したパスキーは、TOTP を外すと使えるようになる。admin のパスキーを消せるのは本人だけ (D8)。案: 「TOTP を解除する」の前に AdminGetUserAuthFactors で WEB_AUTHN を確かめ、あれば解除ではなく D8 手順 2 (削除と再招待) にする。または、admin の行に WEB_AUTHN があることを画面に出す。この点は ADR のD8 / D9 で決着した (「TOTP の解除だけ」は使わず、削除と再招待)。
  - 現在状態の確認 (D9) で TOTP の有無を見るときは UserMFASettingList を使う (ConfiguredUserAuthFactors の SOFTWARE_TOKEN は外した後も残る)。

### 12. パスキーの複数登録と一覧 (D1 / D8)

- 期待: 1 人が 2 つの認証器 (PC とスマホ) でパスキーを登録でき、一覧の名前 (`friendlyCredentialName`) と認証器の種類 (`authenticatorAttachment`) で見分けられ、1 つを消しても残りで入れる。
- やったこと: お客様役がメールのコードで入り、仮想認証器 PC 役 (internal) とスマホ役 (hybrid) を付け替えて、associateWebAuthnCredential を 1 回ずつ呼んだ。同じ認証器での 2 回目も試した。listWebAuthnCredentials を読み、PC 役を deleteWebAuthnCredential で消して、スマホ役と PC 役でサインインを試した。消したパスキーの assertion を、ブラウザの候補を空にして作り、RespondToAuthChallenge に直接渡した。
- 結果:
  - 登録: 2 つとも通った。同じ認証器の 2 回目は、ブラウザが断った (Amplify の PasskeyAlreadyExists。Cognito が登録済みの一覧を渡すため)。
  - 一覧: PC 役は名前「Device-bound Passkey」、種類 platform、transports internal。スマホ役は名前「Roaming passkey」、種類 cross-platform、transports ble / hybrid。どちらも登録日時と RP ID (`localhost`) が返った。
  - AdminGetUserAuthFactors: EMAIL_OTP / WEB_AUTHN (個数は分からない)。
  - PC 役を消した後: 一覧は 1 件。スマホ役でサインインできた。PC 役ではブラウザが合う鍵を見つけられず、失敗した。消した鍵で作った assertion は、Cognito が NotAuthorizedException「Credential does not exist」で拒んだ。
- 判定: 確認できた (仮想認証器で)。
- 影響 (D1):
  - 自動の名前は端末の名前ではなく、種類の名前 (仮想認証器では上の 2 つ)。PC でスマホを使って登録 (hybrid) すれば platform / cross-platform で見分けられるが、スマホのブラウザで直接登録すると、スマホのパスキーも platform になり、PC と同じ種類になる (推論)。同期されるパスキー (端末のパスワード管理に保存されるもの) でどんな名前が付くかは、実機で試していない。
  - 一覧で端末を見分けるには、登録日時に頼ることになる。SERVICE-PLATFORM 側で名前を持つ (台帳に credentialId ごとの名前) かを、詳細設計で決める。
- 未解明の観察: WEB_AUTHN のサインインで Cognito が返す候補 (`allowCredentials`) に、今回の認証器のどれとも合わない項目があった。ハーネスの記録はこの項目を `unknown` に潰すので、transports が internal、ID が 20 バイト、同じ 1 つが入り続けたことは手動観察で、記録からは確かめ直せない。一覧 (listWebAuthnCredentials) には出ない。途中で止めた最初の実行で登録した鍵が残ったものという説明は仮説のままで、確かめられなかった (その鍵はもう持っていない)。ほかに、登録の直後は候補に合わない項目がもう 1 つ増え、そのパスキーを消すと一緒に消えた (記録では `unknown` の数で見える。形が同じことは手動観察)。消したパスキーそのものは候補から消え、Cognito も拒んだので、D8 の前提は崩れない。

### 13. 本人のトークンで呼ぶ API が CloudTrail に残るか (D8 / D12)

- 期待: 本人のアクセストークンで呼ぶ StartWebAuthnRegistration / CompleteWebAuthnRegistration / DeleteWebAuthnCredential / ChangePassword が、アカウントの CloudTrail に記録され、どの利用者のものか分かる。
- やったこと: 各項目で呼んだ時刻を控え、最初の対象 API の呼び出しから 24 分後、最後の対象 API の呼び出しから 18 分後に、CloudTrail の lookup-events をイベント名ごとに引いた。比べるため、ほかの Cognito の API も引いた。
- 結果 (ハーネスが記録したもの):
  - 4 つとも記録された (StartWebAuthnRegistration 7 件、CompleteWebAuthnRegistration 5 件、DeleteWebAuthnCredential 3 件、ChangePassword 1 件。どれも PoC のプールの呼び出しと突き合わせられた)。
  - 呼び出した人: `userIdentity.type` は `Unknown` で、IAM の主体ではない。`additionalEventData.sub` に利用者の sub が入る (お客様役と admin 役のそれぞれの sub と一致)。送信元 IP と User-Agent はある。
  - WAF の Block で止めた ChangePassword (14) は、検索した時点と時間窓 (最後の対象の呼び出しから 18 分後) では検出しなかった。配送の遅れの可能性は残り、常に記録されないとまでは言えない。
  - ほかに、ListWebAuthnCredentials、GetTokensFromRefreshToken、InitiateAuth、RespondToAuthChallenge、RevokeToken、UpdateUserAttributes (拒否も errorCode 付きで)、ForgotPassword (sub 無し)、TOTP の登録の 3 つ、管理 API (IAMUser) も記録された。
- 実行時の手動観察 (ハーネスの記録には残らない。再現には CloudTrail の lookup-events を直接読む):
  - イベントは管理イベントで、`readOnly: false`。
  - `principalId` は sub ではない。
  - 要求の中身: `accessToken` と `proposedPassword` は「HIDDEN_DUE_TO_SECURITY_REASONS」。DeleteWebAuthnCredential には `credentialId` が残る (どのパスキーを消したか分かる)。StartWebAuthnRegistration と CompleteWebAuthnRegistration には、登録したパスキーの ID は残らない (登録したことと時刻、利用者だけ)。
  - プールの ID はイベントに無い (`resources` は空)。利用者は sub で引くことになる。
- 判定: 確認できた。
- 影響 (D8 / D12):
  - パスキーの追加・削除とパスワードの後付けは、SERVICE-PLATFORM の監査テーブルには残らないが、アカウントの CloudTrail で sub ごとに追える。盗難や乗っ取りを調べるときの手がかりになる。
  - sub から user_id を引くには、再招待で変わる前の sub の対応も要る。ADR の D3 / D12 で、再招待で古い sub の行を消さず、無効の印を付けて残すことにした。認証の引き当ては無効の印の付いた行を拒否する (403。ADR D3 / D9)。署名と期限を満たす旧 JWT が SERVICE-PLATFORM 側でも拒否されることは、実装で検証する (項目 8 で確かめたのは Cognito の API の拒否だけ)。
  - CloudTrail のイベント履歴 (lookup-events) は 90 日まで。それより長く残すには、アカウントの証跡 (trail) に頼る (長期保存用の trail を構成する)。
  - WAF で止めた要求は、今回の検索の時点と時間窓では CloudTrail に検出しなかった (配送の遅れの可能性あり)。止めた記録が要るなら WAF のログを使う。
  - 記録が届くまでの時間は測っていない (18 分後には揃っていた)。

### 14. パスワードの後付けと、web ACL で止められるか (D1 / D8)

- 期待: パスワード無しの利用者が ChangePassword (`PreviousPassword` 無し) でパスワードを足せ、`PASSWORD` でサインインでき、AdminGetUserAuthFactors に PASSWORD が出る。ユーザープールの web ACL で ChangePassword を止められ、止めても admin の運用に支障が無い。
- やったこと:
  - パスワード無しのお客様役 (要素 EMAIL_OTP / WEB_AUTHN) が、本人のアクセストークンで Amplify の updatePassword (旧パスワード空) と、SDK の ChangePassword (`PreviousPassword` 省略) を呼んだ。その後、要素とパスワードのサインインを確かめた。
  - web ACL にルール「ヘッダ `x-amz-target` が `AWSCognitoIdentityProviderService.ChangePassword` に完全一致」を置き、最初は Count、スタックのパラメータで Block に替えて、ChangePassword、ほかの公開 API、パスキーのサインイン、AdminSetUserPassword を試した。
  - Block の間に、admin 役の一時パスワード → 本パスワード (NEW_PASSWORD_REQUIRED)、TOTP の登録、本人の ChangePassword、パスワード + TOTP のサインインを試した。最後に Count に戻した。
- 結果:
  - Amplify の updatePassword: 送る前に EmptyUpdatePassword (旧パスワードが要る) で止まる。
  - SDK の ChangePassword (`PreviousPassword` 無し): 通った。要素は PASSWORD / EMAIL_OTP / WEB_AUTHN になり、PASSWORD と PASSWORD_SRP のサインインが `DONE`。
  - Count の間も、ルールはこの要求に一致していた (web ACL の標本に COUNT で記録)。
  - Block: スタックの更新の直後から、ChangePassword は ForbiddenException (HTTP 403)「Request not allowed due to WAF block.」(SDK、Amplify とも)。GetUserAuthFactors、パスキーのサインイン、AdminSetUserPassword (IAM の API) は通った。
  - Block の間の admin: 一時パスワード → 本パスワード (RespondToAuthChallenge)、TOTP の登録 (AssociateSoftwareToken / VerifySoftwareToken / SetUserMFAPreference)、パスワード + TOTP のサインインは通った。本人の ChangePassword だけが 403。
- 判定: 確認できた。
- 影響 (D1):
  - 画面に出さなくても、SDK を直接呼べばパスワードを足せる (Amplify は旧パスワードを求めるが、それは画面側の確認に過ぎない)。
  - 塞ぐなら web ACL のルールで止められ、admin の招待と TOTP の登録は止まらない。止まるのは、admin を含む全員の「自分でパスワードを変える」操作。admin のパスワードの変更は、別の admin の AdminSetUserPassword (`Permanent=false`) で行う (D9 のとおり)。
  - 後付けを見つけるには、AdminGetUserAuthFactors の PASSWORD を使える。
  - web ACL は月の固定費がかかる (調査ノートの料金)。D9 のレート制限の web ACL と同じものにルールを足せば、増えるのはルール 1 本分 (推論)。

## 決定への影響のまとめ

- D1: パスワードの後付けは WAF で ChangePassword を Block する（14）。更新トークンの rotation を採り、ALLOW_REFRESH_TOKEN_AUTH を外す。採用する猶予 30 秒での同時更新は追加検証の対象とする（10）。
- D2: email は必須属性にせず、WriteAttributes を明示する（1・2）。
- D6: 同一 origin・同一クライアントの別バンドルでセッションを共有する（7）。`/camera/` は検証用のサブパスであり、サービス本体はこの公開ツリーに含めない。
- D8 / D9: 管理者の回復は TOTP 解除だけにせず、削除・再招待とする（8・11）。現在の MFA 設定は UserMFASettingList、セッションは mfa_enabled_at と auth_time で検証する（5・6・10・11）。台帳と業務 API の照合はこの PoC の検証範囲に含まない。
- D3 / D12: Cognito 直接操作は CloudTrail の sub から追跡する。再招待前の sub と user_id の対応を無効の印付きで保持する（13）。

## 再現と記録の限界

実機の認証器、同期パスキー、公開 FQDN と CloudFront 経由、SRP と rotation の併用、TOTP を保持したままの一時パスワード変更は、この PoC では検証していない。acr / amr の観測は対象プールに限り、他のプールで同じ結果になるとは限らない。

ハーネスの一部の呼出しは返り値を保存せず、CloudTrail と allowCredentials の一部の属性は手動観察にとどまる。項目 6b は DescribeUserPool の失敗と属性の欠落を区別しないため、その記録だけでは属性がないことを確定できない。各項目で、記録に残る結果・手動観察・推論を区別した。

受信設定は再現環境の POC_IMAP_HOST / POC_IMAP_USER / POC_IMAP_PASSWORD 等で指定する。公開版のハーネスは標準ライブラリの IMAP 接続に置き換えたため、この変更後の AWS・メールを含む再実行は行っていない。手順と保存上の制限は [PoC の README](../../poc/cognito/README.md) を参照する。
