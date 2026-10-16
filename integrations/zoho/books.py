"""Zoho Books v3 read operations: contacts and estimates, org `om` only.

The Books organization id comes from the environment (inside the client); there is
deliberately no parameter for it. No items, taxes, settings or write operations.

Data minimisation (the data goes to an external agent): contact and estimate objects are
reduced to a fixed key allowlist; estimate line items to name, description, quantity,
rate and item_total. Addresses, contact persons, notes and bank data never leave.
"""
from typing import Any

from integrations.zoho.client import ZohoClient
from integrations.zoho.config import BOOKS_ORG
from integrations.zoho.shapes import context, dict_or_none, int_or, list_of_dicts
from integrations.zoho.validation import (
    BOOKS_MAX_PAGE,
    BOOKS_MAX_TEXT,
    PER_PAGE,
    validate_id,
    validate_page,
    validate_status,
    validate_text,
)

CONTACT_KEYS = ("contact_id", "contact_name", "company_name", "email", "phone", "status",
                "outstanding_receivable_amount", "currency_code")
ESTIMATE_KEYS = ("estimate_id", "estimate_number", "date", "expiry_date", "status",
                 "customer_id", "customer_name", "total", "currency_code", "reference_number")
LINE_ITEM_KEYS = ("name", "description", "quantity", "rate", "item_total")


def _pick(source: dict, keys: tuple[str, ...]) -> dict:
    return {key: source[key] for key in keys if key in source}


def _contact(raw: dict) -> dict:
    return _pick(raw, CONTACT_KEYS)


def _estimate(raw: dict) -> dict:
    out = _pick(raw, ESTIMATE_KEYS)
    if "line_items" in raw:
        out["line_items"] = [_pick(item, LINE_ITEM_KEYS)
                             for item in list_of_dicts(raw, "line_items")]
    return out


def _listing(raw: dict | None, key: str, page: int, reduce: Any) -> dict:
    raw = raw or {}
    page_context = context(raw, "page_context")
    return {"data": [reduce(item) for item in list_of_dicts(raw, key)],
            "page": int_or(page_context.get("page"), page),
            "has_more_page": bool(page_context.get("has_more_page", False))}


def _single(raw: dict | None, key: str, reduce: Any) -> dict:
    item = dict_or_none(raw or {}, key)
    return {"data": None if item is None else reduce(item)}


async def search_contacts(client: ZohoClient, text: str, page: int = 1) -> dict:
    params = {"search_text": validate_text(text, "text", 1, BOOKS_MAX_TEXT),
              "page": validate_page(page, BOOKS_MAX_PAGE), "per_page": PER_PAGE}
    raw = await client.get(BOOKS_ORG, "/books/v3/contacts", params)
    return _listing(raw, "contacts", params["page"], _contact)


async def get_contact(client: ZohoClient, contact_id: str) -> dict:
    raw = await client.get(BOOKS_ORG, f"/books/v3/contacts/{validate_id(contact_id)}")
    return _single(raw, "contact", _contact)


async def list_estimates(client: ZohoClient, status: str | None = None,
                         customer_id: str | None = None, page: int = 1) -> dict:
    params: dict[str, Any] = {}
    if status is not None:
        params["status"] = validate_status(status)
    if customer_id is not None:
        params["customer_id"] = validate_id(customer_id, "customer_id")
    params.update(page=validate_page(page, BOOKS_MAX_PAGE), per_page=PER_PAGE)
    raw = await client.get(BOOKS_ORG, "/books/v3/estimates", params)
    return _listing(raw, "estimates", params["page"], _estimate)


async def get_estimate(client: ZohoClient, estimate_id: str) -> dict:
    raw = await client.get(BOOKS_ORG, f"/books/v3/estimates/{validate_id(estimate_id)}")
    return _single(raw, "estimate", _estimate)
