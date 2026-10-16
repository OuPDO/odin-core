"""Zoho Books v3 read operations: contacts and estimates, org `om` only.

The Books organization id comes from the environment (inside the client); there is
deliberately no parameter for it. No items, taxes, settings or write operations.
"""
import re
from typing import Any

from integrations.zoho.client import (
    BOOKS_MAX_PAGE,
    BOOKS_ORG,
    PER_PAGE,
    ZohoClient,
    ZohoRequestError,
    validate_id,
    validate_page,
    validate_text,
)

_STATUS_RE = re.compile(r"[A-Za-z_.]{1,32}")


def _status(value: Any) -> str:
    if not isinstance(value, str) or not _STATUS_RE.fullmatch(value):
        raise ZohoRequestError("status must be 1 to 32 letters, dot or underscore")
    return value


def _listing(raw: Any, key: str, page: int) -> dict:
    raw = raw or {}
    context = raw.get("page_context") or {}
    return {"data": raw.get(key) or [], "page": context.get("page", page),
            "has_more_page": bool(context.get("has_more_page", False))}


async def search_contacts(client: ZohoClient, text: str, page: int = 1) -> dict:
    params = {"search_text": validate_text(text, "text"),
              "page": validate_page(page, BOOKS_MAX_PAGE), "per_page": PER_PAGE}
    return _listing(await client.get(BOOKS_ORG, "/books/v3/contacts", params),
                    "contacts", params["page"])


async def get_contact(client: ZohoClient, contact_id: str) -> dict:
    raw = await client.get(BOOKS_ORG, f"/books/v3/contacts/{validate_id(contact_id)}") or {}
    return {"data": raw.get("contact")}


async def list_estimates(client: ZohoClient, status: str | None = None,
                         customer_id: str | None = None, page: int = 1) -> dict:
    params: dict[str, Any] = {}
    if status is not None:
        params["status"] = _status(status)
    if customer_id is not None:
        params["customer_id"] = validate_id(customer_id, "customer_id")
    params.update(page=validate_page(page, BOOKS_MAX_PAGE), per_page=PER_PAGE)
    return _listing(await client.get(BOOKS_ORG, "/books/v3/estimates", params),
                    "estimates", params["page"])


async def get_estimate(client: ZohoClient, estimate_id: str) -> dict:
    raw = await client.get(BOOKS_ORG, f"/books/v3/estimates/{validate_id(estimate_id)}") or {}
    return {"data": raw.get("estimate")}
