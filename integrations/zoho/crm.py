"""Zoho CRM v6 read operations (Leads, Contacts, Deals, Accounts).

Each function validates every caller-supplied value first, builds the request from
constants and returns a normalized result with continuation metadata
(`page`, `more_records`). Nothing here can write.

Data minimisation (the data goes to an external agent): get and list ask Zoho only for
the per-module DEFAULT_FIELDS unless the caller names fields explicitly; search results
are reduced to the same fields plus `id`; the record owner is reduced to its name.
"""
from typing import Any

from integrations.zoho.client import ZohoClient
from integrations.zoho.errors import ZohoRequestError
from integrations.zoho.shapes import context, int_or, list_of_dicts
from integrations.zoho.validation import (
    CRM_MAX_PAGE,
    CRM_MAX_TEXT,
    CRM_MIN_TEXT,
    NOTES_FIELDS,
    PER_PAGE,
    validate_fields_list,
    validate_id,
    validate_page,
    validate_text,
)

MODULES = ("Leads", "Contacts", "Deals", "Accounts")
SEARCH_QUERY_KEYS = ("word", "email", "phone")

# Field searches are fixed per module; anything else is rejected before the network.
FIELD_SEARCH = {
    "Leads": {"full_name": "Full_Name", "company": "Company"},
    "Contacts": {"full_name": "Full_Name", "account": "Account_Name"},
    "Accounts": {"name": "Account_Name"},
    "Deals": {"name": "Deal_Name"},
}

DEFAULT_FIELDS = {
    "Leads": ("First_Name,Last_Name,Full_Name,Company,Email,Phone,Mobile,Designation,"
              "Lead_Status,Lead_Source,Owner,Created_Time,Modified_Time,Website,City"),
    "Contacts": ("First_Name,Last_Name,Full_Name,Account_Name,Email,Phone,Mobile,Title,"
                 "Lead_Source,Owner,Created_Time,Modified_Time,Mailing_City"),
    "Deals": "Deal_Name,Stage,Amount,Closing_Date,Account_Name,Contact_Name,Owner",
    "Accounts": ("Account_Name,Website,Phone,Billing_City,Owner,Created_Time,"
                 "Modified_Time"),
}


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


def _slim_owner(record: dict) -> dict:
    owner = record.get("Owner")
    if isinstance(owner, dict):
        return {**record, "Owner": {"name": owner.get("name")}}
    return record


def _records(raw: dict | None, keep: frozenset[str] | None = None) -> list[dict]:
    out = []
    for record in list_of_dicts(raw or {}, "data"):
        if keep is not None:
            record = {k: v for k, v in record.items() if k in keep}
        out.append(_slim_owner(record))
    return out


def _normalize(raw: dict | None, page: int, keep: frozenset[str] | None = None) -> dict:
    info = context(raw or {}, "info")
    return {"data": _records(raw, keep), "page": int_or(info.get("page"), page),
            "more_records": bool(info.get("more_records", False))}


async def search(client: ZohoClient, org: str, module: str, by: str, value: str,
                 page: int = 1) -> dict:
    module = _module(module)
    params = _search_params(module, by, value)
    params.update(page=validate_page(page, CRM_MAX_PAGE), per_page=PER_PAGE)
    keep = frozenset(DEFAULT_FIELDS[module].split(",")) | {"id"}
    raw = await client.get(org, f"/crm/v6/{module}/search", params)
    return _normalize(raw, params["page"], keep)


async def get_record(client: ZohoClient, org: str, module: str, record_id: str,
                     fields: list[str] | None = None) -> dict:
    module = _module(module)
    path = f"/crm/v6/{module}/{validate_id(record_id)}"
    field_param = DEFAULT_FIELDS[module] if fields is None else validate_fields_list(fields)
    return {"data": _records(await client.get(org, path, {"fields": field_param}))}


async def list_records(client: ZohoClient, org: str, module: str,
                       fields: list[str] | None = None, page: int = 1) -> dict:
    module = _module(module)
    field_param = DEFAULT_FIELDS[module] if fields is None else validate_fields_list(fields)
    page = validate_page(page, CRM_MAX_PAGE)
    params = {"fields": field_param, "page": page, "per_page": PER_PAGE}
    return _normalize(await client.get(org, f"/crm/v6/{module}", params), page)


async def list_notes(client: ZohoClient, org: str, module: str, record_id: str,
                     page: int = 1) -> dict:
    module = _module(module)
    path = f"/crm/v6/{module}/{validate_id(record_id)}/Notes"
    page = validate_page(page, CRM_MAX_PAGE)
    params = {"fields": NOTES_FIELDS, "page": page, "per_page": PER_PAGE}
    return _normalize(await client.get(org, path, params), page)
