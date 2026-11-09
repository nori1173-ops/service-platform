"""admin-users の API のエラー（共通のエンベロープの code と HTTP の状態）。"""

VALIDATION_ERROR = "VALIDATION_ERROR"
USER_NOT_FOUND = "USER_NOT_FOUND"
USER_EXISTS = "USER_EXISTS"
SELF_OPERATION_FORBIDDEN = "SELF_OPERATION_FORBIDDEN"
LAST_ADMIN = "LAST_ADMIN"
USE_ADMIN_ACTIVATION = "USE_ADMIN_ACTIVATION"
USER_NOT_SUSPENDED = "USER_NOT_SUSPENDED"
PASSKEY_NOT_REGISTERED = "PASSKEY_NOT_REGISTERED"
ADMIN_NOT_AWAITING = "ADMIN_NOT_AWAITING"
ADMIN_NOT_READY = "ADMIN_NOT_READY"
TARGET_NOT_ADMIN = "TARGET_NOT_ADMIN"
COGNITO_USER_MISSING = "COGNITO_USER_MISSING"
CONFLICT = "CONFLICT"
LEDGER_WRITE_FAILED = "LEDGER_WRITE_FAILED"
OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
REINVITE_INCOMPLETE = "REINVITE_INCOMPLETE"
ADMIN_NOT_ACTIVE = "ADMIN_NOT_ACTIVE"
SUSPEND_INCOMPLETE = "SUSPEND_INCOMPLETE"
TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
MEMBER_NOT_FOUND = "MEMBER_NOT_FOUND"
GROUP_ENDED = "GROUP_ENDED"
GROUP_DEPTH_EXCEEDED = "GROUP_DEPTH_EXCEEDED"
GROUP_NAME_TAKEN = "GROUP_NAME_TAKEN"
GROUP_IN_USE = "GROUP_IN_USE"
MEMBER_EXISTS = "MEMBER_EXISTS"


class ApiError(Exception):
    """利用者に理由を示して断る。メッセージに秘密 (一時パスワード・連絡先) を入れない。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def validation(message: str) -> ApiError:
    return ApiError(400, VALIDATION_ERROR, message)


def not_found() -> ApiError:
    return ApiError(404, USER_NOT_FOUND, "利用者が見つかりません。一覧を更新してください")


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)


def ledger_write_failed() -> ApiError:
    return ApiError(500, LEDGER_WRITE_FAILED, "台帳に書けませんでした。状態を確かめてからやり直してください")


def outcome_unknown() -> ApiError:
    return ApiError(503, OUTCOME_UNKNOWN, "結果を確かめられませんでした。一覧で状態を確かめてください")


def compensation_failed() -> ApiError:
    return ApiError(
        500,
        LEDGER_WRITE_FAILED,
        "台帳に書けず、作った利用者も消せませんでした。"
        "Cognito に利用者が残っている可能性があります。一覧で確かめてください",
    )


def last_admin() -> ApiError:
    return conflict(LAST_ADMIN, "最後の有効な管理者には操作できません。先に別の管理者を有効にしてください")


def caller_not_active() -> ApiError:
    return ApiError(403, ADMIN_NOT_ACTIVE, "管理者として有効ではありません")


def stale() -> ApiError:
    return conflict(CONFLICT, "ほかの操作と重なりました。一覧を更新してからやり直してください")


def reinvite_incomplete() -> ApiError:
    return conflict(REINVITE_INCOMPLETE, "再招待が終わっていません。再招待をやり直してください")


def temporarily_unavailable(message: str) -> ApiError:
    return ApiError(503, TEMPORARILY_UNAVAILABLE, message)


def group_not_found() -> ApiError:
    return ApiError(404, GROUP_NOT_FOUND, "グループが見つかりません。一覧を更新してください")


def member_not_found() -> ApiError:
    return ApiError(404, MEMBER_NOT_FOUND, "この利用者はグループに属していません")


def group_ended() -> ApiError:
    return conflict(GROUP_ENDED, "グループは終了しています")


def group_depth_exceeded() -> ApiError:
    return conflict(GROUP_DEPTH_EXCEEDED, "グループは 3 段までです。3 段目の下には作れません")


def group_name_taken() -> ApiError:
    return conflict(GROUP_NAME_TAKEN, "同じ親の下に同じ名前のグループがあります")


def group_in_use() -> ApiError:
    return conflict(GROUP_IN_USE, "稼働中の子のグループ・所属・終わっていない割り当てがあるため、終えられません")


def member_exists() -> ApiError:
    return conflict(MEMBER_EXISTS, "この利用者はすでにグループに属しています")
