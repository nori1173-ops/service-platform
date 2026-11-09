"""base が使う boto3 クライアント（Lambda のコンテナの間で使い回す）。`reset()` は moto 用。"""

import os

import boto3

_cache: dict[str, object] = {}


def reset() -> None:
    _cache.clear()


def _client(service: str):
    if service not in _cache:
        _cache[service] = boto3.client(service)
    return _cache[service]


def cognito():
    return _client("cognito-idp")


def dynamodb():
    return _client("dynamodb")


def ssm():
    return _client("ssm")


def dynamodb_resource():
    if "dynamodb_resource" not in _cache:
        _cache["dynamodb_resource"] = boto3.resource("dynamodb")
    return _cache["dynamodb_resource"]


def user_pool_id() -> str:
    return os.environ["USER_POOL_ID"]
