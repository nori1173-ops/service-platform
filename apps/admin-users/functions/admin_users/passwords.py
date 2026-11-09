"""admin の一時パスワードの生成（計画 4.2）。

core の PasswordPolicy (12 文字以上、英大小・数字、記号なし) を満たす 20 文字。
一時パスワードは応答で 1 回だけ返し、ログ・監査・台帳・例外のメッセージに入れない。
"""

import secrets
import string

LENGTH = 20
ALPHABET = string.ascii_letters + string.digits


def generate() -> str:
    while True:
        password = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password
