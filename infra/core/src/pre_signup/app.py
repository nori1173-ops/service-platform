"""Cognito PreSignUp トリガー（ADR-001 D2）。

招待 (AdminCreateUser) から呼ばれたときだけ通し、自己登録 (SignUp) と外部の IdP からの作成を拒む。
プールの `AllowAdminCreateUserOnly` と重ねた多重の守り。Cognito は例外が投げられると作成を失敗させる。
"""

ADMIN_CREATE_USER = "PreSignUp_AdminCreateUser"


def handler(event: dict, context) -> dict:
    if event.get("triggerSource") != ADMIN_CREATE_USER:
        raise Exception("Self sign-up is not allowed")
    return event
