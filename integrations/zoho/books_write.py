"""Zoho Books v3 write operations (org `om`): contacts, estimates, items. Never sends mail.

Callers supply only validated `fields` (write_rules.validate_books_fields). There is no
query parameter at all (the organization id is added by the client), no `send`, no portal
invitation, no reminder. Estimates can only be marked sent/accepted/declined (a status
change, Zoho sends nothing). No deletes, invoices, payments or recurring documents.
Results are projected to ids and a few labels; bodies are never echoed.
"""
import logging
from typing import Any

from integrations.zoho.client import ZohoClient
from integrations.zoho.config import BOOKS_ORG
from integrations.zoho.errors import (
    MAX_NUMERIC_CODE,
    ZohoAPIError,
    ZohoOutcomeUnknown,
    ZohoRequestError,
)
from integrations.zoho.shapes import dict_or_none
from integrations.zoho.validation import MARKABLE_STATUSES, validate_id
from integrations.zoho.write_rules import validate_books_fields

log = logging.getLogger("odin.integrations.zoho")

CONTACT_RESULT_KEYS = ("contact_id", "contact_name", "status")
ESTIMATE_RESULT_KEYS = ("estimate_id", "estimate_number", "status", "total", "customer_id")
ITEM_RESULT_KEYS = ("item_id", "name", "rate", "status")


def _project(raw: dict | None, entity: str | None, keys: tuple[str, ...]) -> dict:
    """Projection of a Books write response. A dispatched write without a usable numeric
    code, or a successful create without its entity, is an unknown outcome (raise)."""
    raw = raw or {}
    code = raw.get("code")
    if isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= MAX_NUMERIC_CODE:
        raise ZohoOutcomeUnknown("no usable result code")
    try:
        item = dict_or_none(raw, entity) if entity else None
    except ZohoAPIError:
        raise ZohoOutcomeUnknown("unexpected response shape") from None
    if entity and code == 0 and (item is None or not item.get(keys[0])):
        raise ZohoOutcomeUnknown("no result entity")
    out: dict[str, Any] = {"ok": code == 0, "code": code,
                           "data": None if item is None else {k: item[k] for k in keys
                                                              if k in item}}
    if code != 0:
        out["message"] = f"Zoho Books hat die Anfrage abgelehnt (Code {code})"
    return out


async def _send(client: ZohoClient, method: str, path: str, body: Any, entity: str | None,
                keys: tuple[str, ...], what: str) -> dict:
    result = _project(await client.write(BOOKS_ORG, method, path, body), entity, keys)
    log.info("zoho books write: op=%s ok=%s", what, result["ok"])
    return result


async def create_contact(client: ZohoClient, fields: dict) -> dict:
    body = validate_books_fields(fields, "contacts")
    return await _send(client, "POST", "/books/v3/contacts", body, "contact",
                       CONTACT_RESULT_KEYS, "create_contact")


async def update_contact(client: ZohoClient, contact_id: str, fields: dict) -> dict:
    path = f"/books/v3/contacts/{validate_id(contact_id)}"
    return await _send(client, "PUT", path, validate_books_fields(fields, "contacts"),
                       "contact", CONTACT_RESULT_KEYS, "update_contact")


async def create_estimate(client: ZohoClient, fields: dict) -> dict:
    body = validate_books_fields(fields, "estimates")
    return await _send(client, "POST", "/books/v3/estimates", body, "estimate",
                       ESTIMATE_RESULT_KEYS, "create_estimate")


async def update_estimate(client: ZohoClient, estimate_id: str, fields: dict) -> dict:
    path = f"/books/v3/estimates/{validate_id(estimate_id)}"
    return await _send(client, "PUT", path, validate_books_fields(fields, "estimates"),
                       "estimate", ESTIMATE_RESULT_KEYS, "update_estimate")


async def mark_estimate(client: ZohoClient, estimate_id: str, status: str) -> dict:
    if status not in MARKABLE_STATUSES:
        raise ZohoRequestError(f"status must be one of {', '.join(MARKABLE_STATUSES)}")
    path = f"/books/v3/estimates/{validate_id(estimate_id)}/status/{status}"
    return await _send(client, "POST", path, None, None, (), "mark_estimate")


async def create_item(client: ZohoClient, fields: dict) -> dict:
    body = validate_books_fields(fields, "items")
    return await _send(client, "POST", "/books/v3/items", body, "item", ITEM_RESULT_KEYS,
                       "create_item")
