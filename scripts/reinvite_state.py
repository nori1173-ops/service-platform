"""emergency-reinvite の途中の状態のファイル（ADR-001 D9 の緊急の復旧、計画 4.1）。

一時パスワードのファイルと同じ置き場に、権限 600 の JSON で置く。結果不明で止まったときだけ残り、
同じ `--output-dir` で流し直したときに読む。持つのは、送りかけた台帳のトランザクション
(段の名前、ClientRequestToken、要求の中身、作り直した利用者の sub、そのときの一時パスワードのファイルの場所) だけ。
一時パスワードそのものは入れない。

送りかけた要求は、流し直したときに同じ token と同じ中身で送り直す。DynamoDB は 10 分の間、
同じ token の同じ要求を冪等に扱う (中身が違うと IdempotentParameterMismatchException になる)。
"""

import json
import os
import pathlib
import uuid
from dataclasses import dataclass


@dataclass
class ReinviteState:
    path: pathlib.Path
    env: str
    user_id: str
    pending: dict | None = None

    @staticmethod
    def path_for(directory: pathlib.Path, env: str, user_id: str) -> pathlib.Path:
        return directory / f"emergency-reinvite-{env}-{user_id}.json"

    @classmethod
    def load(cls, directory: pathlib.Path, env: str, user_id: str) -> "ReinviteState | None":
        path = cls.path_for(directory, env, user_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("env") != env or data.get("user_id") != user_id:
            raise ValueError(f"状態のファイルの中身が対象と合いません: {path}")
        return cls(path=path, env=env, user_id=user_id, pending=data.get("pending"))

    @classmethod
    def open(cls, directory: pathlib.Path, env: str, user_id: str) -> "ReinviteState":
        path = cls.path_for(directory, env, user_id)
        return cls.load(directory, env, user_id) or cls(path=path, env=env, user_id=user_id)

    def pending_for(self, step: str, new_sub: str | None = None) -> dict | None:
        pending = self.pending
        if pending and pending.get("step") == step and pending.get("new_sub") == new_sub:
            return pending
        return None

    def remember(self, step: str, items: list[dict], *, new_sub: str | None = None, secret_path=None) -> dict:
        """送る前に、段と新しい token と要求の中身を書き残す。"""
        self.pending = {
            "step": step,
            "token": uuid.uuid4().hex,
            "items": items,
            "new_sub": new_sub,
            "secret_path": str(secret_path) if secret_path else None,
        }
        self.save()
        return self.pending

    def point_to(self, secret_path) -> None:
        """送りかけた段の一時パスワードのファイルの場所を、今の回のものに書き換える (冪等性には関わらない)。"""
        if self.pending is not None:
            self.pending = {**self.pending, "secret_path": str(secret_path)}
            self.save()

    def forget(self) -> None:
        self.pending = None
        self.save()

    def save(self) -> None:
        data = {"env": self.env, "user_id": self.user_id, "pending": self.pending}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.write("\n")
        os.chmod(self.path, 0o600)

    def remove(self) -> None:
        if self.path.exists():
            self.path.unlink()
