"""Zoho CRM v6 read operations (Leads, Contacts, Deals, Accounts).

Each function validates every caller-supplied value first, builds the request from
constants and returns a normalized result with continuation metadata
(`page`, `more_records`). Nothing here can write.
"""
from typing import Any

from integrations.zoho.client import (
    CRM_MAX_PAGE,
    PER_PAGE,
    ZohoClient,
    ZohoRequestError,
    validate_id,
    validate_page,
    validate_text,
)

MODULES = ("Leads", "Contacts", "Deals", "Accounts")
SEARCH_QUERY_KEYS = ("word", "email", "phone")
MAX_FIELDS = 50

# Field searches are fixed per module; anything else is rejected before the network.
FIELD_SEARCH = {
    "Leads": {"full_name": "Full_Name", "company": "Company"},
    "Contacts": {"full_name": "Full_Name", "account": "Account_Name"},
    "Accounts": {"name": "Account_Name"},
    "Deals": {"name": "Deal_Name"},
}

DEFAULT_LIST_FIELDS = {
    "Leads": "Full_Name,Company,Email,Phone,Lead_Status",
    "Contacts": "Full_Name,Email,Phone,Account_Name",
    "Deals": "Deal_Name,Stage,Amount,Account_Name,Closing_Date",
    "Accounts": "Account_Name,Website,Phone",
}

_FIELD_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


def _module(value: Any) -> str:
    if value not in MODULES:  # exact, case-sensitive match against the enum
        raise ZohoRequestError(f"module must be one of {', '.join(MODULES)}")
    return value


def _escape_criteria(value: str) -> str:
    out = value.replace("\\", "\\\\")
    for char in "(),":
        out = out.replace(char, "\\" + char)
    return out


def _validate_fields(fields: Any) -> str:
    if not isinstance(fields, (list, tuple)) or not 1 <= len(fields) <= MAX_FIELDS:
        raise ZohoRequestError(f"fields must be a list of 1 to {MAX_FIELDS} field names")
    for name in fields:
        ok = isinstance(name, str) and 1 <= len(name) <= 64 and set(name) <= _FIELD_NAME_CHARS
        if not ok:
            raise ZohoRequestError("field names may only contain letters, digits and underscore")
    return ",".join(fields)


def _search_params(module: str, by: Any, value: Any) -> dict:
    text = validate_text(value, "value")
    if by in SEARCH_QUERY_KEYS:
        return {by: text}
    field_name = FIELD_SEARCH[module].get(by) if isinstance(by, str) else None
    if field_name is None:
        allowed = ", ".join(SEARCH_QUERY_KEYS + tuple(FIELD_SEARCH[module]))
        raise ZohoRequestError(f"by must be one of {allowed} for {module}")
    return {"criteria": f"({field_name}:equals:{_escape_criteria(text)})"}


def _normalize(raw: Any, page: int) -> dict:
    raw = raw or {}
    info = raw.get("info") or {}
    return {"data": raw.get("data") or [], "page": info.get("page", page),
            "more_records": bool(info.get("more_records", False))}


async def search(client: ZohoClient, org: str, module: str, by: str, value: str,
                 page: int = 1) -> dict:
    module = _module(module)
    params = _search_params(module, by, value)
    params.update(page=validate_page(page, CRM_MAX_PAGE), per_page=PER_PAGE)
    return _normalize(await client.get(org, f"/crm/v6/{module}/search", params), params["page"])


async def get_record(client: ZohoClient, org: str, module: str, record_id: str) -> dict:
    module = _module(module)
    path = f"/crm/v6/{module}/{validate_id(record_id)}"
    raw = await client.get(org, path) or {}
    return {"data": raw.get("data") or []}


async def list_records(client: ZohoClient, org: str, module: str,
                       fields: list[str] | None = None, page: int = 1) -> dict:
    module = _module(module)
    field_param = DEFAULT_LIST_FIELDS[module] if fields is None else _validate_fields(fields)
    page = validate_page(page, CRM_MAX_PAGE)
    params = {"fields": field_param, "page": page, "per_page": PER_PAGE}
    return _normalize(await client.get(org, f"/crm/v6/{module}", params), page)


async def list_notes(client: ZohoClient, org: str, module: str, record_id: str,
                     page: int = 1) -> dict:
    module = _module(module)
    path = f"/crm/v6/{module}/{validate_id(record_id)}/Notes"
    page = validate_page(page, CRM_MAX_PAGE)
    return _normalize(await client.get(org, path, {"page": page, "per_page": PER_PAGE}), page)
