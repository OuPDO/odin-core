"""Shape checks for upstream payloads: anything unexpected is a clean ZohoAPIError."""
from typing import Any

from integrations.zoho.errors import ZohoAPIError

UNEXPECTED = "Zoho API returned an unexpected response shape"


def list_of_dicts(raw: dict, key: str) -> list[dict]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ZohoAPIError(UNEXPECTED)
    return value


def dict_or_none(raw: dict, key: str) -> dict | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, dict):
        raise ZohoAPIError(UNEXPECTED)
    return value


def context(raw: dict, key: str) -> dict:
    return dict_or_none(raw, key) or {}


def int_or(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
