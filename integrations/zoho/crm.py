"""Zoho CRM v6 read operations (Leads, Contacts, Deals, Accounts).

Each function validates every caller-supplied value first, builds the request from
constants and returns a normalized result with continuation metadata
(`page`, `more_records`). Nothing here can write.

Data minimisation (the data goes to an external agent): callers cannot choose fields.
Requests carry the fixed per-module field list; EVERY response (get, list, search, notes)
is projected to that list plus `id`. Lookup objects (Owner, Account_Name, ...) are reduced
to their `name`; lists and other nested values are dropped. Notes keep id, title, content,
timestamps and the owner name.
"""
from typing import Any

from integrations.zoho.client import ZohoClient
from integrations.zoho.errors import ZohoRequestError
from integrations.zoho.shapes import context, int_or, list_of_dicts
from integrations.zoho.validation import (
    CRM_MAX_PAGE,
    CRM_MAX_TEXT,
    CRM_MIN_TEXT,
    MODULE_FIELDS,
    NOTES_FIELDS,
    PER_PAGE,
    validate_id,
    validate_page,
    validate_text,
)

MODULES = tuple(MODULE_FIELDS)
SEARCH_QUERY_KEYS = ("word", "email", "phone")
DEFAULT_FIELDS = MODULE_FIELDS

# Field searches are fixed per module; anything else is rejected before the network.
FIELD_SEARCH = {
    "Leads": {"full_name": "Full_Name", "company": "Company"},
    "Contacts": {"full_name": "Full_Name", "account": "Account_Name"},
    "Accounts": {"name": "Account_Name"},
    "Deals": {"name": "Deal_Name"},
}

_NOTE_KEEP = frozenset(NOTES_FIELDS.split(",")) | {"id"}
_MODULE_KEEP = {module: frozenset(fields.split(",")) | {"id"}
                for module, fields in MODULE_FIELDS.items()}


def _module(value: Any) -> str:
    if value not in MODULES:  # exact, case-sensitive match against the enum
        raise ZohoRequestError(f"module must be one of {', '.join(MODULES)}")
    return value


def _escape_criteria(value: str) -> str:
    out = value.replace("\\", "\\\\")
    for char in "(),":
        out = out.replace(char, "\\" + char)
    return out


def _search_params(module: str, by: Any, value: Any) -> dict:
    text = validate_text(value, "value", CRM_MIN_TEXT, CRM_MAX_TEXT)
    if by in SEARCH_QUERY_KEYS:
        return {by: text}
    field_name = FIELD_SEARCH[module].get(by) if isinstance(by, str) else None
    if field_name is None:
        allowed = ", ".join(SEARCH_QUERY_KEYS + tuple(FIELD_SEARCH[module]))
        raise ZohoRequestError(f"by must be one of {allowed} for {module}")
    return {"criteria": f"({field_name}:equals:{_escape_criteria(text)})"}


def _project(record: dict, keep: frozenset[str]) -> dict:
    """Allowlisted keys only; objects shrink to their name, lists and other nesting vanish."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key not in keep:
            continue
        if isinstance(value, dict):
            out[key] = {"name": value.get("name")}
        elif not isinstance(value, list):
            out[key] = value
    return out


def _normalize(raw: dict | None, page: int, keep: frozenset[str]) -> dict:
    raw = raw or {}
    info = context(raw, "info")
    return {"data": [_project(r, keep) for r in list_of_dicts(raw, "data")],
            "page": int_or(info.get("page"), page),
            "more_records": bool(info.get("more_records", False))}


async def search(client: ZohoClient, org: str, module: str, by: str, value: str,
                 page: int = 1) -> dict:
    module = _module(module)
    params = _search_params(module, by, value)
    params.update(page=validate_page(page, CRM_MAX_PAGE), per_page=PER_PAGE)
    raw = await client.get(org, f"/crm/v6/{module}/search", params)
    return _normalize(raw, params["page"], _MODULE_KEEP[module])


async def get_record(client: ZohoClient, org: str, module: str, record_id: str) -> dict:
    module = _module(module)
    path = f"/crm/v6/{module}/{validate_id(record_id)}"
    raw = await client.get(org, path, {"fields": MODULE_FIELDS[module]})
    return {"data": _normalize(raw, 1, _MODULE_KEEP[module])["data"]}


async def list_records(client: ZohoClient, org: str, module: str, page: int = 1) -> dict:
    module = _module(module)
    page = validate_page(page, CRM_MAX_PAGE)
    params = {"fields": MODULE_FIELDS[module], "page": page, "per_page": PER_PAGE}
    raw = await client.get(org, f"/crm/v6/{module}", params)
    return _normalize(raw, page, _MODULE_KEEP[module])


async def list_notes(client: ZohoClient, org: str, module: str, record_id: str,
                     page: int = 1) -> dict:
    module = _module(module)
    path = f"/crm/v6/{module}/{validate_id(record_id)}/Notes"
    page = validate_page(page, CRM_MAX_PAGE)
    params = {"fields": NOTES_FIELDS, "page": page, "per_page": PER_PAGE}
    return _normalize(await client.get(org, path, params), page, _NOTE_KEEP)
