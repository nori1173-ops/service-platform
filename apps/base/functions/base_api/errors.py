"""base の API のエラー (共通のエンベロープの code と HTTP の状態)。"""


class ApiError(Exception):
    """利用者に理由を示して断る。メッセージに入力の値 (暗証番号など) を入れない。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def validation(message: str) -> ApiError:
    return ApiError(400, "VALIDATION_ERROR", message)


def conflict() -> ApiError:
    return ApiError(409, "CONFLICT", "ほかの操作と重なりました。もう一度お試しください")


def temporarily_unavailable(message: str) -> ApiError:
    return ApiError(503, "TEMPORARILY_UNAVAILABLE", message)
