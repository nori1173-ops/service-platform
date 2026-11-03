"""SERVICE-PLATFORM Cognito PoC  の 14 項目を dev の PoC スタックで確かめる。

使い方:
  python run_poc.py --only 1          # 1 項目だけ
  python run_poc.py --only 2,3,9      # 複数
  python run_poc.py --list            # 項目の一覧

前提: deploy.sh で PoC スタックを作り、web で npm run build を済ませていること。
ID・トークン・鍵はコードに書かず、スタックの Outputs と out/ (git 管理外) から読む。
ブラウザの状態 (localStorage と仮想認証器の鍵) は out/state.json に保存し、次の実行で戻す。
"""
from __future__ import annotations

import argparse
import base64
import email
import functools
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import string
import struct
import imaplib
from email.header import decode_header, make_header
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SITE = HERE / "web" / "site"
OUT = HERE / "out"
STATE = OUT / "state.json"
RESULTS = OUT / "results.json"
EVENTS = OUT / "events.json"
PORT = int(os.environ.get("POC_PORT", "8765"))
ORIGIN = f"http://localhost:{PORT}"
STACK = os.environ.get("STACK", "service-platform-poc-cognito-dev")
REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
# 招待とメールコードは、検証者が管理する専用のテスト宛先へ送る。
TEST_MAIL = os.environ.get("POC_TEST_MAIL", "")
MAIL_FROM_DOMAIN = os.environ.get("SES_DOMAIN", "ses-sender-dev.example-cloud.com")

AUTHENTICATORS = {
    "pc": {"protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
           "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True},
    "phone": {"protocol": "ctap2", "transport": "hybrid", "hasResidentKey": True,
              "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True},
    "admin": {"protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
              "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True},
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(*a):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *a, flush=True)


def gen_password() -> str:
    body = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))
    return "Pp9" + body


def totp(secret_b32: str, t: float | None = None) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((t or time.time()) // 30)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def fresh_totp(secret: str) -> str:
    # 30 秒の窓の終わり近くなら次の窓まで待つ (同じコードの再利用も避ける)
    remain = 30 - (time.time() % 30)
    if remain < 6:
        time.sleep(remain + 1)
    return totp(secret)


def decode_jwt(tok: str) -> dict:
    p = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


# ---------------------------------------------------------------- 秘匿の置き換え

class Redactor:
    def __init__(self):
        self.map: dict[str, str] = {}

    def add(self, value, label):
        if value and isinstance(value, str) and value not in self.map:
            self.map[value] = label

    def __call__(self, obj):
        s = json.dumps(obj, ensure_ascii=False, default=str)
        s = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "<jwt>", s)
        s = re.sub(r"eyJ[A-Za-z0-9_-]{40,}[A-Za-z0-9_.-]*", "<token>", s)
        for v, label in sorted(self.map.items(), key=lambda kv: -len(kv[0])):
            s = s.replace(v, label)
        s = re.sub(r"\b\d{12}\b", "<account>", s)
        s = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", s)
        return json.loads(s)


# ---------------------------------------------------------------- メール (IMAP)

class Mail:
    """環境変数の設定で IMAP over TLS を読む（BODY.PEEK で既読にしない）。"""

    def __init__(self):
        self.host = os.environ.get("POC_IMAP_HOST", "")
        self.user = os.environ.get("POC_IMAP_USER", "")
        self.password = os.environ.get("POC_IMAP_PASSWORD", "")
        self.port = int(os.environ.get("POC_IMAP_PORT", "993"))
        self.mailbox = os.environ.get("POC_IMAP_MAILBOX", "INBOX")
        self.ok = bool(self.host and self.user and self.password)

    def _conn(self):
        c = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            c.login(self.user, self.password)
            status, _ = c.select(self.mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError("IMAP mailbox selection failed")
            return c
        except Exception:
            c.logout()
            raise

    def _search(self, c):
        since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        typ, data = c.uid("search", None, "FROM", f'"{MAIL_FROM_DOMAIN}"', "SINCE", since)
        return set(data[0].split()) if typ == "OK" else set()

    def baseline(self) -> set:
        if not self.ok:
            return set()
        c = self._conn()
        try:
            return self._search(c)
        finally:
            c.logout()

    def wait(self, base: set, sent_at: float, timeout=240, interval=5):
        """新しいメールを待ち、(経過秒, 件名, 本文テキスト, Date) を返す。"""
        if not self.ok:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            c = self._conn()
            try:
                new = sorted(self._search(c) - base, key=int)
                if new:
                    uid = new[-1]
                    typ, data = c.uid("fetch", uid, "(BODY.PEEK[])")
                    raw = next(x[1] for x in data if isinstance(x, tuple))
                    msg = email.message_from_bytes(raw)
                    text = ""
                    for part in msg.walk():
                        if part.get_content_maintype() == "text":
                            payload = part.get_payload(decode=True) or b""
                            text += payload.decode(part.get_content_charset() or "utf-8", "replace")
                    return {
                        "elapsed_s": round(time.time() - sent_at, 1),
                        "subject": str(make_header(decode_header(msg.get("Subject", "")))),
                        "date": msg.get("Date"),
                        "text": re.sub(r"<[^>]+>", " ", text),
                    }
            finally:
                c.logout()
            time.sleep(interval)
        return {"elapsed_s": None, "timeout": timeout}


def extract_code(text: str) -> str | None:
    m = re.search(r"\b(\d{6,8})\b", text or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- 静的サーバー

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def start_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(QuietHandler, directory=str(SITE)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- 本体

class Ctx:
    def __init__(self, pw):
        raw = json.loads((SITE / "config.raw.json").read_text())
        self.cfg = {o["OutputKey"]: o["OutputValue"] for o in raw}
        (SITE / "config.json").write_text(json.dumps(self.cfg))
        self.pool = self.cfg["UserPoolId"]
        self.idp = boto3.client("cognito-idp", region_name=REGION)
        self.red = Redactor()
        self.red.add(self.pool, "<pool>")
        self.red.add(self.cfg["WebClientId"], "<client>")
        self.red.add(self.cfg["RotationClientId"], "<rotation-client>")
        self.red.add(TEST_MAIL, "<test-mail>")
        self.mail = Mail()
        OUT.mkdir(exist_ok=True)
        self.state = json.loads(STATE.read_text()) if STATE.exists() else {"creds": {}, "users": {}}
        for sub, label in self.state.setdefault("subs", {}).items():
            self.red.add(sub, label)
        self.results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
        self.events = json.loads(EVENTS.read_text()) if EVENTS.exists() else []
        self.browser = pw.chromium.launch(headless=True)
        storage = self.state.get("storage")
        self.context = self.browser.new_context(storage_state=storage) if storage else self.browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(ORIGIN + "/")
        self.client_key = "WebClientId"
        self.ev("init", "WebClientId")
        self.cdp = self.context.new_cdp_session(self.page)
        self.cdp.send("WebAuthn.enable", {"enableUI": False})
        self.attached: dict[str, str] = {}
        self.transports: dict[str, str] = {}

    # --- 記録
    def record(self, n: int, data: dict):
        data = self.red(data)
        data["at"] = now()
        self.results[str(n)] = data
        RESULTS.write_text(json.dumps(self.results, ensure_ascii=False, indent=2))
        log(f"PoC {n}:", json.dumps(data, ensure_ascii=False)[:3000])

    def mark(self, name: str, **kw):
        self.events.append({"name": name, "time": now(), **kw})
        EVENTS.write_text(json.dumps(self.events, ensure_ascii=False, indent=2))

    def save(self):
        for name in list(self.attached):
            self.detach(name)
        self.state["storage"] = self.context.storage_state()
        STATE.write_text(json.dumps(self.state))

    # --- ブラウザ
    def ev(self, fn: str, *args):
        return self.page.evaluate("([fn, args]) => window.poc[fn](...args)", [fn, list(args)])

    def use_client(self, key: str):
        if self.client_key != key:
            self.ev("init", key)
            self.client_key = key

    def attach(self, name: str):
        for other in list(self.attached):
            if other != name:
                self.detach(other)
        if name in self.attached:
            return
        opts = dict(AUTHENTICATORS[name])
        try:
            aid = self.cdp.send("WebAuthn.addVirtualAuthenticator", {"options": opts})["authenticatorId"]
        except Exception:  # noqa: BLE001  古い Chromium は hybrid を知らない
            opts["transport"] = "usb"
            aid = self.cdp.send("WebAuthn.addVirtualAuthenticator", {"options": opts})["authenticatorId"]
        self.transports[name] = opts["transport"]
        for c in self.state["creds"].get(name, []):
            self.cdp.send("WebAuthn.addCredential", {"authenticatorId": aid, "credential": c})
        self.attached[name] = aid

    def detach(self, name: str):
        aid = self.attached.pop(name, None)
        if not aid:
            return
        creds = self.cdp.send("WebAuthn.getCredentials", {"authenticatorId": aid})["credentials"]
        self.state["creds"][name] = creds
        self.cdp.send("WebAuthn.removeVirtualAuthenticator", {"authenticatorId": aid})

    def cred_ids(self, name: str) -> list[str]:
        if name in self.attached:
            creds = self.cdp.send("WebAuthn.getCredentials", {"authenticatorId": self.attached[name]})["credentials"]
        else:
            creds = self.state["creds"].get(name, [])
        return [c["credentialId"] for c in creds]

    def sign_out(self):
        return self.ev("signOut")

    # --- Cognito (管理側)
    def user(self, username: str):
        try:
            return self.idp.admin_get_user(UserPoolId=self.pool, Username=username)
        except self.idp.exceptions.UserNotFoundException:
            return None

    def admin_call(self, op: str, body: dict) -> dict:
        """SDK に無い操作 (boto3 1.42 系に AdminGetUserAuthFactors が無い) を SigV4 で直接呼ぶ。"""
        from botocore.auth import SigV4Auth  # noqa: PLC0415
        from botocore.awsrequest import AWSRequest  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415
        url = f"https://cognito-idp.{REGION}.amazonaws.com/"
        req = AWSRequest(method="POST", url=url, data=json.dumps(body), headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": f"AWSCognitoIdentityProviderService.{op}"})
        SigV4Auth(boto3.Session().get_credentials().get_frozen_credentials(), "cognito-idp", REGION).add_auth(req)
        r = urllib.request.Request(url, data=req.body if isinstance(req.body, bytes) else req.body.encode(),
                                   headers=dict(req.headers.items()), method="POST")
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"error": json.loads(e.read() or b"{}"), "status": e.code}

    def factors(self, username: str):
        r = self.admin_call("AdminGetUserAuthFactors", {"UserPoolId": self.pool, "Username": username})
        if "error" in r:
            return r
        return {"ConfiguredUserAuthFactors": r.get("ConfiguredUserAuthFactors"),
                "PreferredMfaSetting": r.get("PreferredMfaSetting"),
                "UserMFASettingList": r.get("UserMFASettingList")}

    def delete_user(self, username: str):
        if self.user(username):
            self.idp.admin_delete_user(UserPoolId=self.pool, Username=username)
            self.mark("AdminDeleteUser", user=username)

    def add_sub(self, username: str):
        u = self.user(username)
        if u:
            sub = next(a["Value"] for a in u["UserAttributes"] if a["Name"] == "sub")
            subs = self.state.setdefault("subs", {})
            if sub not in subs:
                role = self.state["users"].get(username, {}).get("role", "user")
                subs[sub] = f"<sub:{role}:{len(subs) + 1}>"
                STATE.write_text(json.dumps(self.state))
            self.red.add(sub, subs[sub])
            return sub
        return None

    # --- サインインの部品
    def email_otp_sign_in(self, username: str, label: str):
        """EMAIL_OTP でサインインし、届いたコードを IMAP で読んで返す。"""
        self.sign_out()
        base = self.mail.baseline()
        t0 = time.time()
        r1 = self.ev("signIn", {"username": username,
                                "options": {"authFlowType": "USER_AUTH", "preferredChallenge": "EMAIL_OTP"}})
        out = {"signIn": r1}
        step = (r1.get("value") or {}).get("nextStep", {}).get("signInStep")
        if step != "CONFIRM_SIGN_IN_WITH_EMAIL_CODE":
            return out
        mail = self.mail.wait(base, t0)
        out["mail"] = {k: v for k, v in (mail or {}).items() if k != "text"}
        code = extract_code((mail or {}).get("text", ""))
        if mail and mail.get("text"):
            out["mail"]["body_shape"] = re.sub(r"\d{6,8}", "<code>", " ".join(mail["text"].split()))[:300]
        if not code:
            out["error"] = "code not received"
            return out
        out["confirm"] = self.ev("confirmSignIn", {"challengeResponse": code})
        self.mark(f"EMAIL_OTP sign-in ({label})", user=username)
        return out

    def passkey_sign_in(self, username: str, authenticator: str):
        self.sign_out()
        self.attach(authenticator)
        return self.ev("signIn", {"username": username,
                                  "options": {"authFlowType": "USER_AUTH", "preferredChallenge": "WEB_AUTHN"}})

    def password_sign_in(self, username: str, password: str, challenge="PASSWORD"):
        self.sign_out()
        return self.ev("signIn", {"username": username, "password": password,
                                  "options": {"authFlowType": "USER_AUTH", "preferredChallenge": challenge}})

    def claims(self):
        t = self.ev("tokens")
        if not t.get("ok") or not t.get("value"):
            return t
        idc = t["value"]["idClaims"]
        ac = t["value"]["accessClaims"]
        return {"id": {k: idc.get(k) for k in ("acr", "amr", "auth_time", "iat", "email_verified")},
                "access": {k: ac.get(k) for k in ("acr", "amr", "auth_time", "iat", "scope")}}

    # --- 利用者の用意
    def ensure_customer(self, invite=True):
        """お客様 C1 (パスワード無し、宛先はテスト用のメール)。"""
        u = TEST_MAIL
        if self.user(u):
            role = self.state["users"].get(u, {}).get("role")
            if role == "customer":
                self.add_sub(u)
                return None
            raise SystemExit(f"{u} は別の役 ({role}) で使用中。先に片付ける")
        base = self.mail.baseline()
        t0 = time.time()
        kw = {"DesiredDeliveryMediums": ["EMAIL"]} if invite else {"MessageAction": "SUPPRESS"}
        self.idp.admin_create_user(
            UserPoolId=self.pool, Username=u,
            UserAttributes=[{"Name": "email", "Value": u}, {"Name": "email_verified", "Value": "true"}], **kw)
        self.state["users"][u] = {"role": "customer"}
        self.mark("AdminCreateUser (customer, passwordless)", user=u, invite=invite)
        self.add_sub(u)
        info = {"status": self.user(u)["UserStatus"]}
        if invite:
            mail = self.mail.wait(base, t0)
            info["invite_mail"] = {k: v for k, v in (mail or {}).items() if k != "text"}
            if mail and mail.get("text"):
                body = re.sub(r"\S+@\S+", "<addr>", " ".join(mail["text"].split()))
                body = re.sub(r"(password is:?\s*)\S+", r"\1<masked>", body, flags=re.I)
                info["invite_mail"]["body_shape"] = re.sub(r"\d{6,8}", "<code>", body)[:400]
        self.state["users"][u]["created"] = info
        STATE.write_text(json.dumps(self.state))
        return info

    def customer_signed_in(self):
        """C1 がサインイン済みでなければ、パスキー (あれば) か EMAIL_OTP で入る。"""
        self.use_client("WebClientId")
        cur = self.ev("getCurrentUser")
        if cur.get("ok"):
            return {"already": True}
        for name in ("phone", "pc"):
            if self.state["creds"].get(name):
                r = self.passkey_sign_in(TEST_MAIL, name)
                if (r.get("value") or {}).get("isSignedIn"):
                    return {"passkey": name}
        return self.email_otp_sign_in(TEST_MAIL, "customer")


# ---------------------------------------------------------------- 各項目

def poc1(ctx: Ctx):
    d = ctx.idp.describe_user_pool(UserPoolId=ctx.pool)["UserPool"]
    email_attr = next(a for a in d["SchemaAttributes"] if a["Name"] == "email")
    ctx.record(1, {
        "expect": "UsernameAttributes=[email] のまま Schema の email を Required=false で作成できる",
        "did": "CloudFormation で Schema の email を Required: false、UsernameAttributes: [email] として作成し、DescribeUserPool を読む",
        "result": {"UsernameAttributes": d.get("UsernameAttributes"), "email_schema": email_attr,
                   "UserPoolTier": d.get("UserPoolTier"), "AutoVerifiedAttributes": d.get("AutoVerifiedAttributes")},
    })


def poc2(ctx: Ctx):
    ctx.ensure_customer()
    s = ctx.customer_signed_in()
    t = ctx.ev("tokens")["value"]
    access = t["access"]
    r_ver = ctx.ev("sdk", "UpdateUserAttributes",
                   {"AccessToken": access, "UserAttributes": [{"Name": "email_verified", "Value": "false"}]})
    r_email = ctx.ev("sdk", "UpdateUserAttributes",
                     {"AccessToken": access, "UserAttributes": [{"Name": "email", "Value": TEST_MAIL}]})
    r_name = ctx.ev("sdk", "UpdateUserAttributes",
                    {"AccessToken": access, "UserAttributes": [{"Name": "name", "Value": "poc"}]})
    r_amp = ctx.ev("updateUserAttributes", {"userAttributes": {"email_verified": "false"}})
    attrs = {a["Name"]: a["Value"] for a in ctx.user(TEST_MAIL)["UserAttributes"]}
    client = ctx.idp.describe_user_pool_client(UserPoolId=ctx.pool, ClientId=ctx.cfg["WebClientId"])["UserPoolClient"]
    ctx.record(2, {
        "expect": "WriteAttributes に email / email_verified を入れないとき、本人の UpdateUserAttributes は NotAuthorized",
        "did": "C1 でサインインし、本人のアクセストークンで UpdateUserAttributes (email_verified=false / email=同じ値 / name) を SDK と Amplify から呼ぶ",
        "result": {"signin": s, "WriteAttributes": client.get("WriteAttributes"),
                   "email_verified_false_sdk": r_ver, "email_same_value_sdk": r_email,
                   "name_control_sdk": r_name, "email_verified_false_amplify": r_amp,
                   "after": {"email_verified": attrs.get("email_verified"), "name": attrs.get("name")}},
    })


def poc3(ctx: Ctx):
    ctx.ensure_customer()
    f = ctx.factors(TEST_MAIL)
    cli = boto3.client("cognito-idp", region_name=REGION)
    try:
        r = cli.forgot_password(ClientId=ctx.cfg["WebClientId"], Username=TEST_MAIL)
        res = {"ok": True, "CodeDeliveryDetails": r.get("CodeDeliveryDetails")}
    except ClientError as e:
        res = {"ok": False, "error": e.response["Error"]["Code"], "message": e.response["Error"]["Message"]}
    ctx.mark("ForgotPassword (customer)")
    try:
        r2 = cli.forgot_password(ClientId=ctx.cfg["WebClientId"], Username="nobody@example.invalid")
        res2 = {"ok": True, "CodeDeliveryDetails": r2.get("CodeDeliveryDetails")}
    except ClientError as e:
        res2 = {"ok": False, "error": e.response["Error"]["Code"], "message": e.response["Error"]["Message"]}
    ctx.record(3, {
        "expect": "AccountRecoverySetting=admin_only のプールで、パスワード無しの利用者の ForgotPassword はエラーになり、コードもパスワードも得られない",
        "did": "パスワード無しの C1 (factors を先に確認) に ForgotPassword を呼ぶ。比較に存在しない利用者でも呼ぶ (PreventUserExistenceErrors=ENABLED)",
        "result": {"factors_before": f, "forgot_password_customer": res,
                   "forgot_password_nonexistent": res2,
                   "factors_after": ctx.factors(TEST_MAIL), "status_after": ctx.user(TEST_MAIL)["UserStatus"]},
    })


def poc9(ctx: Ctx):
    st = ctx.state["users"].get(TEST_MAIL) or {}
    info = st.get("created")
    if not info:
        if st.get("role") not in (None, "customer"):
            raise SystemExit("テスト用の宛先が別の役で使用中")
        ctx.delete_user(TEST_MAIL)
        ctx.state["users"].pop(TEST_MAIL, None)
        info = ctx.ensure_customer(invite=True)
    otp = ctx.email_otp_sign_in(TEST_MAIL, "customer")
    claims = ctx.claims()
    attrs = {a["Name"]: a["Value"] for a in ctx.user(TEST_MAIL)["UserAttributes"]}
    acct = boto3.client("sesv2", region_name=REGION).get_account()
    ctx.record(9, {
        "expect": "招待メールとメールのコードが SES (DEVELOPER) から届く。届くまでの時間を測る",
        "did": "AdminCreateUser (一時パスワード無し、DesiredDeliveryMediums=EMAIL) で招待し、続けて USER_AUTH の EMAIL_OTP でサインイン。受信は IMAP で 5 秒ごとに確かめる",
        "result": {"ses_production_access": acct.get("ProductionAccessEnabled"), "invite": info,
                   "email_otp": otp, "claims_after_email_otp": claims,
                   "email_verified": attrs.get("email_verified"),
                   "user_status_after": ctx.user(TEST_MAIL)["UserStatus"]},
    })


def poc7(ctx: Ctx):
    ctx.ensure_customer()
    s = ctx.customer_signed_in()
    keys = ctx.ev("storageKeys")
    cam = ctx.context.new_page()
    cam.goto(ORIGIN + "/camera/")
    r = cam.evaluate("() => window.camera.check()")
    cam_keys = cam.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('CognitoIdentityServiceProvider.')).length")
    cam.close()
    own = ctx.ev("tokens")["value"]["idClaims"]["sub"]
    ctx.record(7, {
        "expect": "同じ origin の /camera/ の別ビルド (別バンドル、同じプールとクライアント) が、土台の保存したセッションを localStorage から読める (新しいタブ)",
        "did": "/ で C1 がサインインした後、同じブラウザの新しいタブで /camera/ を開き、別バンドルの Amplify で getCurrentUser と fetchAuthSession を呼ぶ",
        "result": {"signin": s, "base_storage_keys": len(keys), "camera_storage_keys": cam_keys,
                   "camera": r, "same_sub": r.get("sub") == own},
    })


def poc12(ctx: Ctx):
    ctx.ensure_customer()
    s = ctx.customer_signed_in()
    out = {"signin": s}
    # 再実行のため、前の実行で登録したものを消してから始める
    for c in (ctx.ev("listWebAuthnCredentials", {}).get("value") or {}).get("credentials", []):
        ctx.ev("deleteWebAuthnCredential", {"credentialId": c["credentialId"]})
    for name in ("pc", "phone"):
        ctx.detach(name)
        ctx.state["creds"][name] = []
    ctx.attach("pc")
    out["register_pc"] = ctx.ev("associateWebAuthnCredential")
    ctx.mark("Start/CompleteWebAuthnRegistration (customer pc)")
    out["register_pc_again_same_authenticator"] = ctx.ev("associateWebAuthnCredential")
    ctx.attach("phone")
    out["register_phone"] = ctx.ev("associateWebAuthnCredential")
    ctx.mark("Start/CompleteWebAuthnRegistration (customer phone)")
    lst = ctx.ev("listWebAuthnCredentials", {})
    pc_ids, phone_ids = set(ctx.cred_ids("pc")), set(ctx.cred_ids("phone"))
    creds = (lst.get("value") or {}).get("credentials", [])

    def which(c):
        cid = c.get("credentialId", "")
        pad = cid + "=" * (-len(cid) % 4)
        for name, ids in (("pc", pc_ids), ("phone", phone_ids)):
            for i in ids:
                if base64.b64decode(i + "=" * (-len(i) % 4)) == base64.urlsafe_b64decode(pad):
                    return name
        return "?"
    out["list"] = [{"device": which(c), "friendlyCredentialName": c.get("friendlyCredentialName"),
                    "authenticatorAttachment": c.get("authenticatorAttachment"),
                    "authenticatorTransports": c.get("authenticatorTransports"),
                    "relyingPartyId": c.get("relyingPartyId"), "createdAt": c.get("createdAt")} for c in creds]
    out["factors_with_2"] = ctx.factors(TEST_MAIL)
    pc_cred = next((c for c in creds if which(c) == "pc"), None)
    if pc_cred:
        out["delete_pc"] = ctx.ev("deleteWebAuthnCredential", {"credentialId": pc_cred["credentialId"]})
        ctx.mark("DeleteWebAuthnCredential (customer pc)")
    out["list_after_delete"] = len((ctx.ev("listWebAuthnCredentials", {}).get("value") or {}).get("credentials", []))
    out["signin_phone_after_delete"] = ctx.passkey_sign_in(TEST_MAIL, "phone")
    out["claims_phone"] = ctx.claims()
    out["signin_pc_after_delete"] = ctx.passkey_sign_in(TEST_MAIL, "pc")
    if (out["signin_pc_after_delete"].get("value") or {}).get("isSignedIn") is not True:
        ctx.sign_out()
        out["signin_phone_again"] = ctx.passkey_sign_in(TEST_MAIL, "phone").get("value", {}).get("isSignedIn")
    out["virtual_authenticator_transports"] = ctx.transports
    ctx.record(12, {
        "expect": "1 人が 2 つの認証器 (PC=platform、スマホ=hybrid) でパスキーを登録でき、一覧で見分けられ、1 つを消しても残りで入れる",
        "did": "C1 でサインインし、CDP の仮想認証器 pc (internal) と phone (cable=hybrid) を順に付け替えて associateWebAuthnCredential。同じ認証器での 2 回目も試す。listWebAuthnCredentials を読み、pc を消して phone / pc でサインインを試す",
        "result": out,
    })


def poc12b(ctx: Ctx):
    """消したパスキーの assertion を Cognito が拒むか (12 の補足)。"""
    out = {}
    ctx.sign_out()
    ctx.attach("pc")
    r = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {
        "USERNAME": TEST_MAIL, "PREFERRED_CHALLENGE": "WEB_AUTHN"}})
    if not r.get("ok") or r["value"].get("ChallengeName") != "WEB_AUTHN":
        out["initiate"] = r
    else:
        opts = json.loads(r["value"]["ChallengeParameters"]["CREDENTIAL_REQUEST_OPTIONS"])
        out["allowCredentials_count"] = len(opts.get("allowCredentials") or [])
        out["userVerification"] = opts.get("userVerification")
        out["rpId"] = opts.get("rpId")
        opts["allowCredentials"] = []
        a = ctx.ev("webauthnGet", json.dumps(opts))
        out["browser_assertion_with_deleted_pc"] = {"ok": a.get("ok"), "error": a.get("error")}
        if a.get("ok"):
            rr = ctx.ev("sdk", "RespondToAuthChallenge", {
                "ChallengeName": "WEB_AUTHN", "Session": r["value"]["Session"],
                "ChallengeResponses": {"USERNAME": TEST_MAIL, "CREDENTIAL": a["value"]}})
            out["respond_with_deleted_credential"] = {"ok": rr.get("ok"), "error": rr.get("error"), "message": rr.get("message")}
    prev = ctx.results.get("12", {})
    prev.setdefault("result", {})["deleted_credential_server_check"] = ctx.red(out)
    ctx.results["12"] = prev
    RESULTS.write_text(json.dumps(ctx.results, ensure_ascii=False, indent=2))
    log("PoC 12b:", json.dumps(ctx.red(out), ensure_ascii=False))


def allow_credential_owners(ctx: Ctx, extra: dict[str, list[bytes]] | None = None):
    r = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {
        "USERNAME": TEST_MAIL, "PREFERRED_CHALLENGE": "WEB_AUTHN"}})
    if not r.get("ok") or r["value"].get("ChallengeName") != "WEB_AUTHN":
        return {"initiate": r}
    opts = json.loads(r["value"]["ChallengeParameters"]["CREDENTIAL_REQUEST_OPTIONS"])
    norm = lambda i: base64.urlsafe_b64decode(i + "=" * (-len(i) % 4))  # noqa: E731
    known = {n: [base64.b64decode(c + "=" * (-len(c) % 4)) for c in ctx.cred_ids(n)] for n in ("pc", "phone")}
    known.update(extra or {})
    return [[n for n, ids in known.items() if norm(c["id"]) in ids] or ["unknown"] for c in opts.get("allowCredentials", [])]


def poc12c(ctx: Ctx):
    """パスキーを消した直後に、USER_AUTH の allowCredentials から消えるか (12 の補足)。"""
    out = {}
    ctx.customer_signed_in()
    out["before"] = allow_credential_owners(ctx)
    ctx.attach("pc")
    reg = ctx.ev("associateWebAuthnCredential")
    out["register_pc_new"] = reg.get("ok")
    new_ids = [base64.b64decode(c + "=" * (-len(c) % 4)) for c in ctx.cred_ids("pc")]
    lst = (ctx.ev("listWebAuthnCredentials", {}).get("value") or {}).get("credentials", [])
    norm = lambda i: base64.urlsafe_b64decode(i + "=" * (-len(i) % 4))  # noqa: E731
    target = [c for c in lst if norm(c["credentialId"]) in new_ids]
    out["after_register"] = allow_credential_owners(ctx, {"pc_new": new_ids})
    for c in target:
        out["delete"] = ctx.ev("deleteWebAuthnCredential", {"credentialId": c["credentialId"]}).get("ok")
    ctx.mark("DeleteWebAuthnCredential (customer pc, 12c)")
    out["list_after_delete"] = len((ctx.ev("listWebAuthnCredentials", {}).get("value") or {}).get("credentials", []))
    out["allow_0s"] = allow_credential_owners(ctx, {"pc_new": new_ids})
    time.sleep(30)
    out["allow_30s"] = allow_credential_owners(ctx, {"pc_new": new_ids})
    time.sleep(60)
    out["allow_90s"] = allow_credential_owners(ctx, {"pc_new": new_ids})
    prev = ctx.results.get("12", {})
    prev.setdefault("result", {})["allow_credentials_after_delete"] = ctx.red(out)
    ctx.results["12"] = prev
    RESULTS.write_text(json.dumps(ctx.results, ensure_ascii=False, indent=2))
    log("PoC 12c:", json.dumps(ctx.red(out), ensure_ascii=False))


def poc10(ctx: Ctx):
    ctx.ensure_customer()
    out = {}
    if not ctx.state["creds"].get("phone"):
        raise SystemExit("PoC 12 で phone のパスキーを先に作る")
    ctx.sign_out()
    ctx.use_client("RotationClientId")
    out["signin_rotation_client"] = ctx.passkey_sign_in(TEST_MAIL, "phone")
    rt0 = ctx.ev("refreshTokenFromStorage")
    t0 = ctx.ev("tokens")["value"]["accessClaims"]
    time.sleep(2)
    out["force_refresh_1"] = {k: v for k, v in ctx.ev("tokens", True).items() if k != "value"}
    rt1 = ctx.ev("refreshTokenFromStorage")
    t1 = ctx.ev("tokens")["value"]["accessClaims"]
    out["refresh_token_rotated"] = bool(rt0 and rt1 and rt0 != rt1)
    out["access_iat_changed"] = t0.get("iat") != t1.get("iat")
    out["auth_time_kept"] = t0.get("auth_time") == t1.get("auth_time")
    ctx.mark("GetTokensFromRefreshToken (rotation client)")
    out["old_refresh_token_reuse"] = ctx.ev("sdk", "GetTokensFromRefreshToken", {"RefreshToken": rt0})
    out["refresh_token_auth_on_rotation_client"] = ctx.ev(
        "sdk", "InitiateAuth", {"AuthFlow": "REFRESH_TOKEN_AUTH", "AuthParameters": {"REFRESH_TOKEN": rt1}})
    out["force_refresh_2"] = {k: v for k, v in ctx.ev("tokens", True).items() if k != "value"}
    ctx.sign_out()
    ctx.use_client("WebClientId")
    out["signin_web_client"] = ctx.passkey_sign_in(TEST_MAIL, "phone")
    w0 = ctx.ev("refreshTokenFromStorage")
    out["web_client_force_refresh"] = {k: v for k, v in ctx.ev("tokens", True).items() if k != "value"}
    out["web_client_refresh_token_changed"] = w0 != ctx.ev("refreshTokenFromStorage")
    rc = ctx.idp.describe_user_pool_client(UserPoolId=ctx.pool, ClientId=ctx.cfg["RotationClientId"])["UserPoolClient"]
    out["rotation_client_flows"] = rc.get("ExplicitAuthFlows")
    ctx.record(10, {
        "expect": "Amplify v6 (6.20) の更新が GetTokensFromRefreshToken を使い、rotation を有効にしたクライアントで更新トークンが替わり、古い更新トークンは使えなくなる",
        "did": "Amplify 6.20 のソース (refreshAuthTokens) を確認。rotation 有効 (猶予 0 秒) のクライアントで C1 がパスキーで入り、fetchAuthSession({forceRefresh:true}) を 2 回。古い更新トークンの再利用と REFRESH_TOKEN_AUTH も試す。rotation 無しのクライアントでも同じ更新を試す",
        "result": out,
    })


def poc14(ctx: Ctx):
    ctx.ensure_customer()
    out = {"factors_before": ctx.factors(TEST_MAIL)}
    out["signin"] = ctx.customer_signed_in()
    access = ctx.ev("tokens")["value"]["access"]
    new_pw = gen_password()
    if "PASSWORD" not in (out["factors_before"]["ConfiguredUserAuthFactors"] or []):
        out["amplify_updatePassword_empty_old"] = ctx.ev("updatePassword", {"oldPassword": "", "newPassword": new_pw})
        if not out["amplify_updatePassword_empty_old"].get("ok"):
            out["sdk_ChangePassword_without_previous"] = ctx.ev(
                "sdk", "ChangePassword", {"AccessToken": access, "ProposedPassword": new_pw})
        ctx.mark("ChangePassword (customer, no PreviousPassword)")
        ctx.state["users"][TEST_MAIL]["password"] = new_pw
        STATE.write_text(json.dumps(ctx.state))
    pw = ctx.state["users"][TEST_MAIL].get("password")
    out["factors_after"] = ctx.factors(TEST_MAIL)
    out["password_signin"] = ctx.password_sign_in(TEST_MAIL, pw)
    out["password_signin_srp"] = ctx.password_sign_in(TEST_MAIL, pw, "PASSWORD_SRP")
    out["claims_password"] = ctx.claims()
    ctx.record(14, {
        "expect": "パスワード無しの利用者が ChangePassword (PreviousPassword 無し) でパスワードを足せ、PASSWORD でサインインでき、AdminGetUserAuthFactors に PASSWORD が出る",
        "did": "C1 (パスワード無し) が本人のアクセストークンで Amplify updatePassword (oldPassword 空) / SDK ChangePassword (PreviousPassword 省略) を呼び、AdminGetUserAuthFactors とパスワードのサインインを確かめる。WAF の部分は poc14_waf",
        "result": out,
    })


def set_rule(action: str):
    cf = boto3.client("cloudformation", region_name=REGION)
    params = [{"ParameterKey": k, "UsePreviousValue": True} for k in ("ProjectName", "RpId", "SesDomain")]
    params.append({"ParameterKey": "ChangePasswordRuleAction", "ParameterValue": action})
    try:
        cf.update_stack(StackName=STACK, UsePreviousTemplate=True, Parameters=params,
                        Tags=[{"Key": "Project", "Value": "service-platform"}, {"Key": "Name", "Value": "PoC"}])
    except ClientError as e:
        if "No updates" not in str(e):
            raise
    cf.get_waiter("stack_update_complete").wait(StackName=STACK)


def poc14_waf_admin(ctx: Ctx):
    """14 の後半の補足: ChangePassword を止めた間に、admin の一時パスワードの変更と TOTP の登録ができるか。"""
    st = ensure_admin(ctx)
    out = {}
    set_rule("Block")
    try:
        tmp = gen_password()
        ctx.idp.admin_set_user_password(UserPoolId=ctx.pool, Username=TEST_MAIL, Password=tmp, Permanent=False)
        r = ctx.password_sign_in(TEST_MAIL, tmp, "PASSWORD")
        out["temp_signin_step"] = (r.get("value") or {}).get("nextStep", {}).get("signInStep") or r.get("error")
        if out["temp_signin_step"] == "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED":
            pw = gen_password()
            c = ctx.ev("confirmSignIn", {"challengeResponse": pw})
            out["new_password_while_blocked"] = (c.get("value") or {}).get("isSignedIn") if c.get("ok") else c
            if c.get("ok"):
                st["password"] = pw
                STATE.write_text(json.dumps(ctx.state))
        s2 = ctx.ev("setUpTOTP")
        if s2.get("ok"):
            secret = s2["value"]["sharedSecret"]
            out["verifyTOTPSetup_while_blocked"] = ctx.ev("verifyTOTPSetup", {"code": fresh_totp(secret)}).get("ok")
            out["updateMFAPreference_while_blocked"] = ctx.ev("updateMFAPreference", {"totp": "PREFERRED"}).get("ok")
            st["totp"] = secret
            st["last_totp_window"] = int(time.time() // 30)
            STATE.write_text(json.dumps(ctx.state))
        else:
            out["setUpTOTP_while_blocked"] = s2
        out["admin_change_password_while_blocked"] = {k: v for k, v in ctx.ev("updatePassword", {"oldPassword": st["password"], "newPassword": gen_password()}).items() if k in ("ok", "error")}
        ctx.sign_out()
        pt = admin_password_totp_sign_in(ctx)
        out["password_totp_after"] = (pt.get("confirm") or pt.get("signIn") or {}).get("value", {}).get("isSignedIn")
        ctx.sign_out()
    finally:
        set_rule("Count")
    prev = ctx.results.get("14", {})
    prev.setdefault("result", {})["waf_admin"] = ctx.red(out)
    ctx.results["14"] = prev
    RESULTS.write_text(json.dumps(ctx.results, ensure_ascii=False, indent=2))
    log("PoC 14 (WAF admin):", json.dumps(ctx.red(out), ensure_ascii=False))


def poc14_waf(ctx: Ctx):
    """ユーザープールの web ACL で ChangePassword を止められるか (14 の後半)。"""
    out = {}

    ctx.ensure_customer()
    ctx.customer_signed_in()
    pw = ctx.state["users"][TEST_MAIL].get("password")
    if not pw:
        raise SystemExit("PoC 14 を先に")
    access = ctx.ev("tokens")["value"]["access"]
    set_rule("Block")
    try:
        t_block = time.time()
        out["rule"] = "Block"
        tries = []
        for i in range(12):
            np = gen_password()
            r = ctx.ev("sdk", "ChangePassword", {"AccessToken": access, "PreviousPassword": pw, "ProposedPassword": np})
            tries.append({"after_s": round(time.time() - t_block), "ok": r.get("ok"), "error": r.get("error"),
                          "status": r.get("status"), "message": (r.get("message") or "")[:200]})
            if r.get("ok"):
                pw = np
                ctx.state["users"][TEST_MAIL]["password"] = pw
                STATE.write_text(json.dumps(ctx.state))
            else:
                break
            time.sleep(10)
        out["change_password_tries"] = tries
        out["amplify_updatePassword_blocked"] = ctx.ev(
            "updatePassword", {"oldPassword": pw, "newPassword": gen_password()})
        out["other_public_api_GetUserAuthFactors"] = ctx.ev("sdk", "GetUserAuthFactors", {"AccessToken": access})
        out["passkey_signin_while_blocked"] = (
            ctx.passkey_sign_in(TEST_MAIL, "phone").get("value") or {}).get("isSignedIn")
        tmp = gen_password()
        try:
            ctx.idp.admin_set_user_password(UserPoolId=ctx.pool, Username=TEST_MAIL, Password=tmp, Permanent=True)
            out["AdminSetUserPassword_while_blocked"] = "ok"
            ctx.state["users"][TEST_MAIL]["password"] = tmp
            STATE.write_text(json.dumps(ctx.state))
        except ClientError as e:
            out["AdminSetUserPassword_while_blocked"] = e.response["Error"]["Code"]
        ctx.mark("AdminSetUserPassword (customer, while WAF block)")
        wafv2 = boto3.client("wafv2", region_name=REGION)
        try:
            acl = next(a for a in wafv2.list_web_acls(Scope="REGIONAL")["WebACLs"]
                       if a["Name"] == ctx.cfg["WebAclName"])
            end = datetime.now(timezone.utc)
            sampled = wafv2.get_sampled_requests(
                WebAclArn=acl["ARN"], RuleMetricName="change-password", Scope="REGIONAL",
                TimeWindow={"StartTime": end - timedelta(hours=2), "EndTime": end}, MaxItems=20)
            out["waf_sampled"] = [{"action": s.get("Action"), "uri": s["Request"].get("URI"),
                                   "target": next((h["Value"] for h in s["Request"]["Headers"] if h["Name"].lower() == "x-amz-target"), None)}
                                  for s in sampled.get("SampledRequests", [])]
        except Exception as e:  # noqa: BLE001
            out["waf_sampled_error"] = str(e)[:200]
    finally:
        set_rule("Count")
    out["rule_after"] = "Count"
    prev = ctx.results.get("14", {})
    prev.setdefault("result", {})["waf"] = ctx.red(out)
    prev["did"] = prev.get("did", "") + "。後半: スタックのパラメータで web ACL のルール (x-amz-target が ChangePassword に一致) を Block にし、ChangePassword と他の公開 API、パスキーのサインイン、AdminSetUserPassword を試し、Count に戻す"
    ctx.results["14"] = prev
    RESULTS.write_text(json.dumps(ctx.results, ensure_ascii=False, indent=2))
    log("PoC 14 (WAF):", json.dumps(ctx.red(out), ensure_ascii=False)[:3000])


def poc8(ctx: Ctx):
    ctx.ensure_customer()
    out = {"factors_before": ctx.factors(TEST_MAIL)}
    ctx.customer_signed_in()
    old_access = ctx.ev("tokens")["value"]["access"]
    ctx.delete_user(TEST_MAIL)
    ctx.state["users"].pop(TEST_MAIL, None)
    ctx.idp.admin_create_user(
        UserPoolId=ctx.pool, Username=TEST_MAIL, MessageAction="SUPPRESS",
        UserAttributes=[{"Name": "email", "Value": TEST_MAIL}, {"Name": "email_verified", "Value": "true"}])
    ctx.state["users"][TEST_MAIL] = {"role": "customer", "recreated": True}
    STATE.write_text(json.dumps(ctx.state))
    ctx.mark("AdminCreateUser (customer re-invite, SUPPRESS)")
    out["factors_after_recreate"] = ctx.factors(TEST_MAIL)
    out["old_access_token_GetUserAuthFactors"] = ctx.ev("sdk", "GetUserAuthFactors", {"AccessToken": old_access})
    out["passkey_signin_phone_after_recreate"] = ctx.passkey_sign_in(TEST_MAIL, "phone")
    sdk_sel = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {"USERNAME": TEST_MAIL}})
    out["available_challenges_after_recreate"] = (sdk_sel.get("value") or {}).get("AvailableChallenges") or sdk_sel
    ctx.sign_out()
    ctx.record(8, {
        "expect": "AdminDeleteUser → 同じメールアドレスで AdminCreateUser すると、前のパスキー (と後付けのパスワード) は新しい利用者に残らない",
        "did": "パスキー (phone) と後付けのパスワードを持つ C1 を AdminDeleteUser し、同じアドレスで作り直す (SUPPRESS)。AdminGetUserAuthFactors、前のアクセストークン、前のパスキーでのサインイン、USER_AUTH の候補を確かめる",
        "result": out,
    })


def ensure_admin(ctx: Ctx):
    u = TEST_MAIL
    st = ctx.state["users"].get(u, {})
    if ctx.user(u) and st.get("role") == "admin":
        ctx.add_sub(u)
        return st
    if ctx.user(u):
        ctx.delete_user(u)
    tmp = gen_password()
    ctx.idp.admin_create_user(
        UserPoolId=ctx.pool, Username=u, TemporaryPassword=tmp, MessageAction="SUPPRESS",
        UserAttributes=[{"Name": "email", "Value": u}, {"Name": "email_verified", "Value": "true"}])
    ctx.idp.admin_add_user_to_group(UserPoolId=ctx.pool, Username=u, GroupName="admin")
    ctx.state["users"][u] = {"role": "admin", "temp": tmp}
    ctx.mark("AdminCreateUser (admin, temp password, SUPPRESS)")
    STATE.write_text(json.dumps(ctx.state))
    ctx.add_sub(u)
    return ctx.state["users"][u]


def poc4(ctx: Ctx):
    st = ensure_admin(ctx)
    out = {"status": ctx.user(TEST_MAIL)["UserStatus"]}
    r = ctx.password_sign_in(TEST_MAIL, st["temp"], "PASSWORD")
    out["signin_PASSWORD_temp"] = r
    step = (r.get("value") or {}).get("nextStep", {}).get("signInStep")
    if step == "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED":
        pw = gen_password()
        out["confirm_new_password"] = ctx.ev("confirmSignIn", {"challengeResponse": pw})
        st["password"] = pw
        STATE.write_text(json.dumps(ctx.state))
    out["status_after"] = ctx.user(TEST_MAIL)["UserStatus"]
    out["claims"] = ctx.claims()
    tmp2 = gen_password()
    ctx.idp.admin_set_user_password(UserPoolId=ctx.pool, Username=TEST_MAIL, Password=tmp2, Permanent=False)
    r2 = ctx.password_sign_in(TEST_MAIL, tmp2, "PASSWORD_SRP")
    out["signin_PASSWORD_SRP_temp"] = r2
    if (r2.get("value") or {}).get("nextStep", {}).get("signInStep") == "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED":
        pw2 = gen_password()
        out["confirm_new_password_srp"] = ctx.ev("confirmSignIn", {"challengeResponse": pw2})
        st["password"] = pw2
        STATE.write_text(json.dumps(ctx.state))
    ctx.record(4, {
        "expect": "USER_AUTH (preferredChallenge PASSWORD / PASSWORD_SRP) で一時パスワードの利用者が CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED に進み、confirmSignIn で本パスワードを決められる",
        "did": "admin 候補 A1 を一時パスワード・SUPPRESS で作り、Amplify の signIn (USER_AUTH、PASSWORD) → confirmSignIn。AdminSetUserPassword (Permanent=false) で一時パスワードに戻し、PASSWORD_SRP でも同じことを試す",
        "result": out,
    })


def admin_totp(ctx: Ctx, out: dict):
    st = ctx.state["users"][TEST_MAIL]
    if st.get("totp"):
        return
    r = ctx.password_sign_in(TEST_MAIL, st["password"], "PASSWORD")
    out["signin_before_totp"] = (r.get("value") or {}).get("isSignedIn")
    out["claims_password_only"] = ctx.claims()
    s = ctx.ev("setUpTOTP")
    secret = s["value"]["sharedSecret"]
    out["verifyTOTPSetup"] = ctx.ev("verifyTOTPSetup", {"code": fresh_totp(secret)})
    out["updateMFAPreference"] = ctx.ev("updateMFAPreference", {"totp": "PREFERRED"})
    st["totp"] = secret
    st["last_totp_window"] = int(time.time() // 30)
    STATE.write_text(json.dumps(ctx.state))
    ctx.mark("TOTP setup (admin)")


def next_totp(ctx: Ctx) -> str:
    st = ctx.state["users"][TEST_MAIL]
    while int(time.time() // 30) <= st.get("last_totp_window", 0):
        time.sleep(1)
    code = fresh_totp(st["totp"])
    st["last_totp_window"] = int(time.time() // 30)
    STATE.write_text(json.dumps(ctx.state))
    return code


def admin_password_totp_sign_in(ctx: Ctx):
    st = ctx.state["users"][TEST_MAIL]
    r = ctx.password_sign_in(TEST_MAIL, st["password"], "PASSWORD")
    out = {"signIn": r}
    if (r.get("value") or {}).get("nextStep", {}).get("signInStep") == "CONFIRM_SIGN_IN_WITH_TOTP_CODE":
        out["confirm"] = ctx.ev("confirmSignIn", {"challengeResponse": next_totp(ctx)})
    return out


def poc5(ctx: Ctx):
    st = ensure_admin(ctx)
    if not st.get("password"):
        raise SystemExit("PoC 4 を先に")
    out = {}
    admin_totp(ctx, out)
    out["factors"] = ctx.factors(TEST_MAIL)
    # TOTP 済みのセッションでパスキーを登録してみる
    pt = admin_password_totp_sign_in(ctx)
    out["password_totp"] = pt
    out["claims_password_totp"] = ctx.claims()
    if not ctx.state["creds"].get("admin"):
        ctx.attach("admin")
        out["register_passkey_with_totp"] = ctx.ev("associateWebAuthnCredential")
        ctx.mark("Start/CompleteWebAuthnRegistration (admin)")
    out["factors_after_passkey"] = ctx.factors(TEST_MAIL)
    ctx.sign_out()
    sel = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {"USERNAME": TEST_MAIL}})
    out["select_challenge"] = {k: (sel.get("value") or {}).get(k) for k in ("ChallengeName", "AvailableChallenges")} if sel.get("ok") else sel
    out["passkey_signin"] = ctx.passkey_sign_in(TEST_MAIL, "admin")
    ctx.sign_out()
    base = ctx.mail.baseline()
    t0 = time.time()
    out["email_otp_signin"] = ctx.ev("signIn", {"username": TEST_MAIL, "options": {"authFlowType": "USER_AUTH", "preferredChallenge": "EMAIL_OTP"}})
    if (out["email_otp_signin"].get("value") or {}).get("nextStep", {}).get("signInStep") == "CONFIRM_SIGN_IN_WITH_EMAIL_CODE":
        m = ctx.mail.wait(base, t0, timeout=120)
        out["email_otp_mail"] = {k: v for k, v in (m or {}).items() if k != "text"}
    ctx.sign_out()
    if sel.get("ok") and sel["value"].get("Session"):
        out["respond_select_EMAIL_OTP"] = ctx.ev("sdk", "RespondToAuthChallenge", {
            "ChallengeName": "SELECT_CHALLENGE", "Session": sel["value"]["Session"],
            "ChallengeResponses": {"USERNAME": TEST_MAIL, "ANSWER": "EMAIL_OTP"}})
        if out["respond_select_EMAIL_OTP"].get("ok"):
            out["respond_select_EMAIL_OTP"] = {k: out["respond_select_EMAIL_OTP"]["value"].get(k) for k in ("ChallengeName", "ChallengeParameters")}
    ctx.record(5, {
        "expect": "TOTP を設定した利用者 (プールの MFA は OPTIONAL) は、パスキーとメールのコードではサインインできず、パスワード + TOTP では入れる",
        "did": "A1 がパスワードで入って TOTP を登録 (setUpTOTP → verifyTOTPSetup → updateMFAPreference PREFERRED)。パスワード + TOTP で入り直し、そのセッションでパスキーを登録。USER_AUTH の候補 (SELECT_CHALLENGE)、パスキー、EMAIL_OTP を試す",
        "result": out,
    })


def poc6(ctx: Ctx):
    st = ensure_admin(ctx)
    if not st.get("totp"):
        raise SystemExit("PoC 5 を先に")
    out = {}
    ctx.sign_out()
    # SDK 直接: TARGET_ACR_VALUES を付けた USER_AUTH (パスワード)
    for acr in ("urn:cognito:loa:4", "loa:4"):
        r = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {
            "USERNAME": TEST_MAIL, "PREFERRED_CHALLENGE": "PASSWORD", "PASSWORD": st["password"],
            "TARGET_ACR_VALUES": acr}})
        item = {"initiate": {k: (r.get("value") or {}).get(k) for k in ("ChallengeName",)} if r.get("ok") else r}
        if r.get("ok") and r["value"].get("ChallengeName") == "SOFTWARE_TOKEN_MFA":
            rr = ctx.ev("sdk", "RespondToAuthChallenge", {
                "ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": r["value"]["Session"],
                "ChallengeResponses": {"USERNAME": TEST_MAIL, "SOFTWARE_TOKEN_MFA_CODE": next_totp(ctx)}})
            if rr.get("ok") and rr["value"].get("AuthenticationResult"):
                ar = rr["value"]["AuthenticationResult"]
                idc, ac = decode_jwt(ar["IdToken"]), decode_jwt(ar["AccessToken"])
                item["id"] = {k: idc.get(k) for k in ("acr", "amr", "auth_time")}
                item["access"] = {k: ac.get(k) for k in ("acr", "amr", "auth_time")}
                rt = ar.get("RefreshToken")
                ref = ctx.ev("sdk", "GetTokensFromRefreshToken", {"RefreshToken": rt})
                if ref.get("ok"):
                    idr = decode_jwt(ref["value"]["AuthenticationResult"]["IdToken"])
                    item["after_refresh_id"] = {k: idr.get(k) for k in ("acr", "amr", "auth_time")}
                else:
                    item["after_refresh"] = ref
            else:
                item["respond"] = rr
        out[f"sdk_target_{acr}"] = item
    # SDK 直接: 対象の段階に届かない要求 (パスワードだけで loa:4 を求めず、loa:1 を求める)
    r = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {
        "USERNAME": TEST_MAIL, "PREFERRED_CHALLENGE": "PASSWORD", "PASSWORD": st["password"],
        "TARGET_ACR_VALUES": "urn:cognito:loa:1"}})
    out["sdk_target_loa1_with_totp_user"] = {"ChallengeName": (r.get("value") or {}).get("ChallengeName")} if r.get("ok") else r
    # Amplify (6.20): signIn は AuthParameters に TARGET_ACR_VALUES を渡せない。普通のサインインのクレームを見る
    pt = admin_password_totp_sign_in(ctx)
    out["amplify_password_totp_signin"] = (pt.get("confirm") or pt.get("signIn") or {}).get("ok")
    out["amplify_claims_password_totp"] = ctx.claims()
    ctx.sign_out()
    out["amplify_source_note"] = "aws-amplify 6.20.0 の handleUserAuthFlow は AuthParameters に USERNAME / PASSWORD / SRP_A / PREFERRED_CHALLENGE だけを入れる"
    other = {k: v.get("result", {}).get(f) for k, f in (("9", "claims_after_email_otp"), ("12", "claims_phone"), ("14", "claims_password"), ("4", "claims")) for v in [ctx.results.get(k, {})]}
    out["claims_from_other_items"] = other
    ctx.record(6, {
        "expect": "このプールでトークンに acr / amr が付き、SDK の USER_AUTH に TARGET_ACR_VALUES を渡せる (Amplify は不明)",
        "did": "A1 (パスワード + TOTP) で、SDK (@aws-sdk/client-cognito-identity-provider) の InitiateAuth に TARGET_ACR_VALUES (urn:cognito:loa:4 と loa:4) を付けてサインインし、ID / アクセストークンのクレームと更新後のクレームを見る。Amplify の通常のサインインのクレームと、他の項目 (メールのコード、パスキー、パスワード) のクレームも並べる",
        "result": out,
    })


def poc6b(ctx: Ctx):
    """ACR の機能がこのプールで有効かの補足 (6 の後半)。"""
    st = ensure_admin(ctx)
    out = {}
    d = ctx.admin_call("DescribeUserPool", {"UserPoolId": ctx.pool})
    up = d.get("UserPool", {})
    out["describe_has_AcrConfiguration"] = "AcrConfiguration" in up
    out["AcrConfiguration"] = up.get("AcrConfiguration")
    for label, params in (("bogus_target", {"TARGET_ACR_VALUES": "urn:example:no-such-level"}),
                          ("bad_max_age", {"TARGET_ACR_VALUES": "urn:cognito:loa:4", "MAX_AGE": "not-a-number"})):
        r = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {
            "USERNAME": TEST_MAIL, "PREFERRED_CHALLENGE": "PASSWORD", "PASSWORD": st["password"], **params}})
        out[label] = {"ChallengeName": (r.get("value") or {}).get("ChallengeName")} if r.get("ok") else {k: r.get(k) for k in ("error", "message")}
    prev = ctx.results.get("6", {})
    prev.setdefault("result", {})["feature_probe"] = ctx.red(out)
    ctx.results["6"] = prev
    RESULTS.write_text(json.dumps(ctx.results, ensure_ascii=False, indent=2))
    log("PoC 6b:", json.dumps(ctx.red(out), ensure_ascii=False))


def poc11(ctx: Ctx):
    st = ensure_admin(ctx)
    if not st.get("totp"):
        raise SystemExit("PoC 5 を先に")
    out = {}
    old_pw = st["password"]
    tmp = gen_password()
    ctx.idp.admin_set_user_password(UserPoolId=ctx.pool, Username=TEST_MAIL, Password=tmp, Permanent=False)
    ctx.mark("AdminSetUserPassword Permanent=false (admin)")
    out["status_after_set"] = ctx.user(TEST_MAIL)["UserStatus"]
    out["signin_old_password"] = ctx.password_sign_in(TEST_MAIL, old_pw, "PASSWORD")
    ctx.sign_out()
    ctx.idp.admin_set_user_mfa_preference(UserPoolId=ctx.pool, Username=TEST_MAIL,
                                         SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False})
    ctx.mark("AdminSetUserMFAPreference TOTP off (admin)")
    out["factors_after_totp_off"] = ctx.factors(TEST_MAIL)
    sel = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {"USERNAME": TEST_MAIL}})
    out["select_challenge"] = {k: (sel.get("value") or {}).get(k) for k in ("ChallengeName", "AvailableChallenges")} if sel.get("ok") else sel
    out["email_otp"] = ctx.email_otp_sign_in(TEST_MAIL, "admin after TOTP off")
    out["claims_email_otp"] = ctx.claims()
    out["status_after_email_otp"] = ctx.user(TEST_MAIL)["UserStatus"]
    ctx.sign_out()
    r = ctx.password_sign_in(TEST_MAIL, tmp, "PASSWORD")
    out["signin_temp_password"] = {"ok": r.get("ok"), "step": (r.get("value") or {}).get("nextStep", {}).get("signInStep"), "error": r.get("error")}
    ctx.sign_out()
    ctx.record(11, {
        "expect": "AdminSetUserPassword (Permanent=false) の後は前のパスワードで入れない。TOTP を外した admin がメールのコードで入れるか (入れるなら解除の間の入口になる)",
        "did": "TOTP 済みの A1 に AdminSetUserPassword (一時パスワード、Permanent=false) → 前のパスワードでサインイン。AdminSetUserMFAPreference で TOTP を外し、USER_AUTH の候補と EMAIL_OTP のサインインを試す。一時パスワードでのサインインの次の段も見る",
        "result": out,
    })


def poc11b(ctx: Ctx):
    """11 の切り分け: 一時パスワードを本パスワードにした後 (CONFIRMED、TOTP は外したまま) の候補と EMAIL_OTP。"""
    st = ensure_admin(ctx)
    out = {}
    tmp = gen_password()
    ctx.idp.admin_set_user_password(UserPoolId=ctx.pool, Username=TEST_MAIL, Password=tmp, Permanent=False)
    r = ctx.password_sign_in(TEST_MAIL, tmp, "PASSWORD")
    if (r.get("value") or {}).get("nextStep", {}).get("signInStep") == "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED":
        pw = gen_password()
        out["confirm_new_password"] = (ctx.ev("confirmSignIn", {"challengeResponse": pw}).get("value") or {}).get("isSignedIn")
        st["password"] = pw
        STATE.write_text(json.dumps(ctx.state))
    ctx.sign_out()
    out["status"] = ctx.user(TEST_MAIL)["UserStatus"]
    out["factors"] = ctx.factors(TEST_MAIL)
    sel = ctx.ev("sdk", "InitiateAuth", {"AuthFlow": "USER_AUTH", "AuthParameters": {"USERNAME": TEST_MAIL}})
    out["select_challenge"] = (sel.get("value") or {}).get("AvailableChallenges") if sel.get("ok") else sel
    if "EMAIL_OTP" in (out["select_challenge"] or []):
        out["email_otp"] = ctx.email_otp_sign_in(TEST_MAIL, "admin CONFIRMED, TOTP off")
        out["claims_email_otp"] = ctx.claims()
        ctx.sign_out()
    if "WEB_AUTHN" in (out["select_challenge"] or []):
        out["passkey_signin"] = ctx.passkey_sign_in(TEST_MAIL, "admin")
        ctx.sign_out()
    prev = ctx.results.get("11", {})
    prev.setdefault("result", {})["after_confirmed"] = ctx.red(out)
    ctx.results["11"] = prev
    RESULTS.write_text(json.dumps(ctx.results, ensure_ascii=False, indent=2))
    log("PoC 11b:", json.dumps(ctx.red(out), ensure_ascii=False))


def poc13(ctx: Ctx):
    ct = boto3.client("cloudtrail", region_name=REGION)
    first = min((e["time"] for e in ctx.events), default=None)
    if not first:
        raise SystemExit("記録する呼び出しがまだ無い")
    start = datetime.fromisoformat(first) - timedelta(minutes=5)
    names = ["StartWebAuthnRegistration", "CompleteWebAuthnRegistration", "DeleteWebAuthnCredential",
             "ChangePassword", "ListWebAuthnCredentials", "GetTokensFromRefreshToken", "InitiateAuth",
             "RespondToAuthChallenge", "AdminDeleteUser", "AdminSetUserPassword", "AdminCreateUser",
             "AssociateSoftwareToken", "VerifySoftwareToken", "SetUserMFAPreference", "UpdateUserAttributes",
             "ForgotPassword", "AdminSetUserMFAPreference", "RevokeToken", "GlobalSignOut"]
    found = {}
    for name in names:
        evs = []
        token = None
        while True:
            kw = {"LookupAttributes": [{"AttributeKey": "EventName", "AttributeValue": name}],
                  "StartTime": start, "EndTime": datetime.now(timezone.utc), "MaxResults": 50}
            if token:
                kw["NextToken"] = token
            r = ct.lookup_events(**kw)
            for e in r.get("Events", []):
                if e.get("EventSource") != "cognito-idp.amazonaws.com":
                    continue
                raw = e["CloudTrailEvent"]
                body = json.loads(raw)
                rp = body.get("requestParameters") or {}
                subs = [s for s, lb in ctx.red.map.items() if lb.startswith("<sub")]
                poc_ids = [ctx.pool, ctx.cfg["WebClientId"], ctx.cfg["RotationClientId"], *subs]
                evs.append({
                    "matches_poc": any(x in raw for x in poc_ids),
                    "eventTime": body.get("eventTime"),
                    "userIdentity_type": (body.get("userIdentity") or {}).get("type"),
                    "userIdentity_keys": sorted((body.get("userIdentity") or {}).keys()),
                    "requestParameters_keys": sorted(rp.keys()) if isinstance(rp, dict) else rp,
                    "additionalEventData": body.get("additionalEventData"),
                    "sub_label": [ctx.red.map[s] for s in subs if s in raw],
                    "errorCode": body.get("errorCode"),
                    "sourceIPAddress_present": bool(body.get("sourceIPAddress")),
                    "userAgent_present": bool(body.get("userAgent")),
                })
            token = r.get("NextToken")
            time.sleep(0.6)
            if not token:
                break
        poc = [x for x in evs if x["matches_poc"]]
        found[name] = {"count_all": len(evs), "count_poc": len(poc), "samples": (poc or evs)[:3]}
    for u, st in ctx.state["users"].items():
        if ctx.user(u):
            ctx.add_sub(u)
    ctx.record(13, {
        "expect": "本人のアクセストークンで呼ぶ StartWebAuthnRegistration / CompleteWebAuthnRegistration / DeleteWebAuthnCredential / ChangePassword が CloudTrail の管理イベントに記録され、どの利用者か分かる",
        "did": "各項目で呼んだ時刻 (out/events.json) の 5 分前から、CloudTrail lookup-events を EventName ごとに引く (最初の呼び出しから 15 分以上たってから実行)",
        "result": {"calls": [{"name": e["name"], "time": e["time"]} for e in ctx.events], "lookup_start": start.isoformat(),
                   "found": found},
    })


ITEMS = {
    "1": poc1, "2": poc2, "3": poc3, "4": poc4, "5": poc5, "6": poc6, "7": poc7, "8": poc8,
    "9": poc9, "10": poc10, "11": poc11, "12": poc12, "13": poc13, "14": poc14, "14waf": poc14_waf, "12b": poc12b, "12c": poc12c, "6b": poc6b, "11b": poc11b, "14wafadmin": poc14_waf_admin,
}
TITLES = {
    "1": "email を利用者名にしたまま必須から外せるか",
    "2": "利用者が UpdateUserAttributes で email_verified を書けないこと",
    "3": "パスワード無しの利用者の ForgotPassword が admin_only で止まるか",
    "4": "USER_AUTH で一時パスワードの利用者が NEW_PASSWORD_REQUIRED に進むか",
    "5": "TOTP の利用者がパスキーとメールのコードで入れず、パスワード + TOTP で入れること",
    "6": "acr / amr と TARGET_ACR_VALUES",
    "6b": "6 の補足: ACR の機能が有効かの確かめ",
    "7": "/camera/ の別ビルドが土台のセッションを読めるか",
    "8": "AdminDeleteUser でパスキーも消えること",
    "9": "招待とメールのコードが SES から届くこと (時間)",
    "10": "更新トークンの rotation を Amplify v6 で使えるか",
    "11": "AdminSetUserPassword (Permanent=false) と TOTP を外した admin",
    "11b": "11 の補足: 本パスワードにした後の候補",
    "12": "パスキーの複数登録、一覧、1 つ削除",
    "12b": "12 の補足: 消したパスキーの assertion を拒むか",
    "12c": "12 の補足: 削除後の allowCredentials",
    "13": "本人のトークンの API が CloudTrail に残るか (最初の呼び出しから 15 分以上後)",
    "14": "パスワード無しの利用者がパスワードを足せるか",
    "14waf": "14 の後半: web ACL で ChangePassword を止められるか",
    "14wafadmin": "14 の後半の補足: 止めた間の admin の一時パスワードの変更と TOTP の登録",
}
ORDER = ["1", "9", "3", "2", "7", "12", "12b", "12c", "10", "14", "14waf", "8", "4", "5", "6", "6b", "11", "11b", "14wafadmin", "13"]


def cleanup(ctx: Ctx):
    for name in list(ctx.attached):
        ctx.detach(name)
    for u in list(ctx.state["users"]):
        ctx.delete_user(u)
    ctx.state["users"] = {}
    ctx.state["creds"] = {}
    ctx.context.clear_cookies()
    ctx.page.evaluate("() => localStorage.clear()")


def main():
    if not TEST_MAIL:
        raise SystemExit("POC_TEST_MAIL (テスト用の宛先) を設定する")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="1 項目 (例 4) かカンマ区切り。14 の後半は 14waf")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--cleanup", action="store_true", help="PoC の利用者を消し、out/state.json を空にする")
    a = ap.parse_args()
    if a.list:
        for k in ORDER:
            print(k, TITLES[k])
        return
    srv = start_server()
    try:
        with sync_playwright() as pw:
            ctx = Ctx(pw)
            try:
                if a.cleanup:
                    cleanup(ctx)
                    return
                for k in (a.only.split(",") if a.only else ORDER):
                    log(f"=== PoC {k} ===")
                    ITEMS[k.strip()](ctx)
            finally:
                ctx.save()
                ctx.browser.close()
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
