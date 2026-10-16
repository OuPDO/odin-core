"""Zoho CRM v6 write operations: create, update, notes, tasks. Never sends mail.

The caller supplies only validated `fields` (write_rules.validate_crm_fields). The request
body is assembled here: record bodies always carry `trigger: []` (no workflow, approval or
blueprint runs) and skip cadences; tasks carry `trigger: []` and no reminder field exists
at all (a reminder is a mail). Owner, tags, layout and every `$` key are not settable.
Results are projected to id, status and a safe code per record; bodies are never echoed.
"""
import logging
import re
from typing import Any

from integrations.zoho.client import ZohoClient
from integrations.zoho.errors import (
    ZohoAPIError,
    ZohoOutcomeUnknown,
    ZohoRequestError,
    safe_code,
)
from integrations.zoho.shapes import dict_or_none, list_of_dicts
from integrations.zoho.validation import is_id, validate_id
from integrations.zoho.write_rules import (
    RECORD_MODULES,
    crm_record_body,
    crm_task_body,
    note_body,
    validate_crm_fields,
)

log = logging.getLogger("odin.integrations.zoho")

_FIELD_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_STATUSES = ("success", "error")


def _module(value: Any) -> str:
    if value not in RECORD_MODULES:  # exact, case-sensitive
        raise ZohoRequestError(f"module must be one of {', '.join(RECORD_MODULES)}")
    return value


def _result(item: dict) -> dict:
    details = dict_or_none(item, "details") or {}
    status = item.get("status")
    out: dict[str, Any] = {
        "id": details["id"] if is_id(details.get("id")) else None,
        "status": status if status in _STATUSES else "unknown",
        "code": safe_code(item.get("code"))}
    field = details.get("api_name")
    if isinstance(field, str) and _FIELD_NAME_RE.fullmatch(field):
        out["field"] = field
    return out


def project(raw: dict | None) -> dict:
    """{ok, results:[{id, status, code[, field]}]} from a Zoho write response.

    The write was already dispatched, so a missing or malformed result list means the
    outcome is unknown (raise), not that nothing happened."""
    try:
        items = list_of_dicts(raw or {}, "data")
        results = [_result(item) for item in items]
    except ZohoAPIError:
        raise ZohoOutcomeUnknown("unexpected response shape") from None
    if not results:
        raise ZohoOutcomeUnknown("no per-record result")
    return {"ok": bool(results) and all(r["status"] == "success" for r in results),
            "results": results}


async def _send(client: ZohoClient, org: str, method: str, path: str, body: dict,
                what: str) -> dict:
    result = project(await client.write(org, method, path, body))
    log.info("zoho crm write: org=%s op=%s ok=%s", org, what, result["ok"])
    return result


async def create(client: ZohoClient, org: str, module: str, fields: dict) -> dict:
    module = _module(module)
    body = crm_record_body(validate_crm_fields(fields))
    return await _send(client, org, "POST", f"/crm/v6/{module}", body, "create")


async def update(client: ZohoClient, org: str, module: str, record_id: str,
                 fields: dict) -> dict:
    module = _module(module)
    path = f"/crm/v6/{module}/{validate_id(record_id)}"
    return await _send(client, org, "PUT", path, crm_record_body(validate_crm_fields(fields)),
                       "update")


async def add_note(client: ZohoClient, org: str, module: str, record_id: str, title: str,
                   content: str) -> dict:
    module = _module(module)
    path = f"/crm/v6/{module}/{validate_id(record_id)}/Notes"
    return await _send(client, org, "POST", path, note_body(title, content), "note")


def _task_record(subject: str, due_date: str, related_module: str | None,
                 related_id: str | None, description: str | None) -> dict:
    record: dict[str, Any] = {"Subject": subject, "Due_Date": due_date}
    if description is not None:
        record["Description"] = description
    if (related_module is None) != (related_id is None):
        raise ZohoRequestError("related_module and related_id must be given together")
    if related_module is not None:
        link = {"id": validate_id(related_id, "related_id")}
        if _module(related_module) == "Contacts":
            record["Who_Id"] = link
        else:
            record.update(What_Id=link, **{"$se_module": related_module})
    return record


async def create_task(client: ZohoClient, org: str, subject: str, due_date: str,
                      related_module: str | None = None, related_id: str | None = None,
                      description: str | None = None) -> dict:
    record = _task_record(subject, due_date, related_module, related_id, description)
    return await _send(client, org, "POST", "/crm/v6/Tasks", crm_task_body(record), "task")
