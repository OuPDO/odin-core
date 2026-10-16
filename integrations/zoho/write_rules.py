"""Rules for everything that leaves odin-core as a Zoho write.

Hard limit: no write may send mail or trigger sending (also not as a side effect: no
workflow, approval, blueprint or cadence trigger, no reminders, no portal invitations).
Two independent layers enforce it, both before any network I/O:

1. Construction: only fixed (method, path, query, body-shape) combinations exist
   (validation.ALLOWED_ENDPOINTS). Record bodies are assembled here with fixed
   `trigger: []` and a cadence skip; callers only supply a validated `fields` dict.
2. Denylist (`check_denied`): any path segment, or token inside a segment, that names a
   mail or notification route is rejected no matter what the allowlist says.

Field rules for caller-supplied `fields` (CRM and Books): keys match a strict pattern, at
most 60 keys; values are str (max 5000 chars, no control characters except newline, tab,
CR), int, float (finite), bool, None or a list of str (max 50). CRM additionally accepts
`{"id": "<digits>"}` lookups for keys ending in `_Name` or `_Id`. Books additionally accepts
`line_items` (estimates) and `contact_persons` (contacts) as lists of flat dicts with fixed
key sets. Forbidden keys are listed below; they cannot be set by callers.
"""
import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from integrations.zoho.errors import ZohoRequestError

MAX_KEYS = 60
MAX_TEXT = 5000
MAX_LIST = 50
MAX_LINE_ITEMS = 100
MAX_CONTACT_PERSONS = 20
MAX_SUBJECT = 255
MAX_NOTE_TITLE = 200
RECORD_MODULES = ("Leads", "Contacts", "Deals", "Accounts")

_CRM_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_BOOKS_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_DIGITS_RE = re.compile(r"[0-9]{1,25}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_TEXT_CONTROLS = frozenset("\n\r\t")

# ---- denylist --------------------------------------------------------------------------------

DENIED_TOKENS = frozenset({
    "email", "emails", "send_mail", "sendmail", "mail", "remind", "reminder", "paymentreminder",
    "statements", "portal", "enable_portal", "invite", "comments", "send", "approve", "submit"})
DENIED_ALWAYS_QUERY_KEYS = frozenset({"send", "send_email", "send_mail"})
MAIL_DENIED = "mail-related route not allowed"


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    return {lowered, *(part for part in _TOKEN_SPLIT_RE.split(lowered) if part)}


def check_denied(method: str, path: str, params: Mapping[str, Any]) -> None:
    """Raise for mail routes (path segments) and send parameters (query keys).

    Reads may use the key `email` (CRM search by e-mail address); only the send keys are
    barred there. Writes bar the whole denylist as query keys.
    """
    for segment in str(path).split("/"):
        if _tokens(segment) & DENIED_TOKENS:
            raise ZohoRequestError(MAIL_DENIED)
    barred = DENIED_ALWAYS_QUERY_KEYS if method == "GET" else DENIED_TOKENS
    for key in params:
        if _tokens(str(key)) & barred:
            raise ZohoRequestError(MAIL_DENIED)


# ---- scalar rules ----------------------------------------------------------------------------

def _is_text(value: Any, max_len: int = MAX_TEXT) -> bool:
    return (isinstance(value, str) and len(value) <= max_len
            and not any((ord(ch) < 32 and ch not in _TEXT_CONTROLS) or ord(ch) == 127
                        for ch in value))


def validate_text_field(value: Any, what: str, max_len: int, *, multiline: bool = False,
                        min_len: int = 1) -> str:
    ok = _is_text(value, max_len) and len(value.strip()) >= min_len
    if ok and not multiline and any(ch in _TEXT_CONTROLS for ch in value):
        ok = False
    if not ok:
        raise ZohoRequestError(f"{what} must be {min_len} to {max_len} printable characters")
    return value


def _scalar_ok(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return _is_text(value)


def _clean_value(key: str, value: Any) -> Any:
    if _scalar_ok(value):
        return value
    if isinstance(value, list) and len(value) <= MAX_LIST and all(_is_text(v) for v in value):
        return list(value)
    raise ZohoRequestError(f"value not allowed for {key}")


def _digits(value: Any, what: str) -> str:
    if not isinstance(value, str) or _DIGITS_RE.fullmatch(value) is None:
        raise ZohoRequestError(f"{what} must be 1 to 25 digits")
    return value


def _lookup(key: str, value: dict) -> dict:
    if set(value) != {"id"}:
        raise ZohoRequestError(f"lookup for {key} must be exactly {{\"id\": digits}}")
    return {"id": _digits(value["id"], f"id in {key}")}


def _check_shape(fields: Any, max_keys: int = MAX_KEYS) -> dict:
    if not isinstance(fields, dict) or not 1 <= len(fields) <= max_keys:
        raise ZohoRequestError(f"fields must be an object with 1 to {max_keys} keys")
    return fields


# ---- CRM -------------------------------------------------------------------------------------

CRM_FORBIDDEN_KEYS = frozenset({
    "owner", "created_by", "modified_by", "tag", "layout", "lar_id", "trigger", "process",
    "approval", "notify", "wf_trigger", "skip_feature_execution", "id", "remind_at",
    "reminder"})
_CRM_FORBIDDEN_FRAGMENTS = ("remind", "notif", "approv")


def _crm_key(key: Any) -> str:
    if not isinstance(key, str) or _CRM_KEY_RE.fullmatch(key) is None:
        raise ZohoRequestError("field names must match [A-Za-z][A-Za-z0-9_]{0,63}")
    lowered = key.lower()
    if (lowered in CRM_FORBIDDEN_KEYS or lowered.startswith("send")
            or any(part in lowered for part in _CRM_FORBIDDEN_FRAGMENTS)):
        raise ZohoRequestError(f"field not allowed: {key}")
    return key


def _is_lookup_key(key: str) -> bool:
    return key.endswith(("_Name", "_Id"))


def validate_crm_fields(fields: Any) -> dict:
    """Validated copy of caller-supplied CRM fields."""
    out: dict[str, Any] = {}
    for key, value in _check_shape(fields).items():
        _crm_key(key)
        if isinstance(value, dict) and _is_lookup_key(key):
            out[key] = _lookup(key, value)
        else:
            out[key] = _clean_value(key, value)
    return out


_RECORD_EXTRAS: dict[str, Any] = {"trigger": [], "skip_feature_execution": [{"name": "cadences"}]}


def crm_record_body(record: dict) -> dict:
    """Body for record create/update: fixed no-trigger and cadence-skip envelope."""
    return {"data": [record], "trigger": [], "skip_feature_execution": [{"name": "cadences"}]}


def _single_record(body: Any, extras: Mapping[str, Any]) -> dict:
    if not isinstance(body, dict) or set(body) != {"data", *extras}:
        raise ZohoRequestError("request body shape not allowed")
    if any(body[key] != value for key, value in extras.items()):
        raise ZohoRequestError("request body shape not allowed")
    data = body["data"]
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ZohoRequestError("request body must carry exactly one record")
    return data[0]


def validate_record_body(body: Any, _module: str | None = None) -> None:
    validate_crm_fields(_single_record(body, _RECORD_EXTRAS))


# Notes: {"data": [{"Note_Title", "Note_Content"}]}; the Notes API takes no trigger list.
def note_body(title: Any, content: Any) -> dict:
    return {"data": [{"Note_Title": validate_text_field(title, "title", MAX_NOTE_TITLE),
                      "Note_Content": validate_text_field(content, "content", MAX_TEXT,
                                                          multiline=True)}]}


def validate_note_body(body: Any, _module: str | None = None) -> None:
    record = _single_record(body, {})
    if set(record) != {"Note_Title", "Note_Content"}:
        raise ZohoRequestError("note must have exactly Note_Title and Note_Content")
    note_body(record["Note_Title"], record["Note_Content"])


# Tasks: fixed keys only; reminder fields are not part of the key set on purpose.
TASK_KEYS = frozenset({"Subject", "Due_Date", "Description", "Who_Id", "What_Id", "$se_module"})


def validate_due_date(value: Any) -> str:
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        raise ZohoRequestError("due_date must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ZohoRequestError("due_date must be a real date (YYYY-MM-DD)") from None
    return value


def validate_task_record(record: Any) -> dict:
    if not isinstance(record, dict) or not {"Subject", "Due_Date"} <= set(record) <= TASK_KEYS:
        raise ZohoRequestError("task fields not allowed")
    validate_text_field(record["Subject"], "subject", MAX_SUBJECT)
    validate_due_date(record["Due_Date"])
    if "Description" in record:
        validate_text_field(record["Description"], "description", MAX_TEXT, multiline=True)
    for key in ("Who_Id", "What_Id"):
        if key in record:
            if not isinstance(record[key], dict):
                raise ZohoRequestError(f"{key} must be a lookup {{\"id\": digits}}")
            _lookup(key, record[key])
    _check_task_relation(record)
    return record


def _check_task_relation(record: dict) -> None:
    who, what, module = "Who_Id" in record, "What_Id" in record, record.get("$se_module")
    if who and (what or module is not None):
        raise ZohoRequestError("a task relates to a contact (Who_Id) or to a record (What_Id)")
    if what != (module is not None) or (what and module not in RECORD_MODULES):
        raise ZohoRequestError("What_Id needs $se_module Leads, Contacts, Deals or Accounts")


def crm_task_body(record: dict) -> dict:
    return {"data": [validate_task_record(record)], "trigger": []}


def validate_task_body(body: Any, _module: str | None = None) -> None:
    validate_task_record(_single_record(body, {"trigger": []}))


# ---- Books -----------------------------------------------------------------------------------

LINE_ITEM_KEYS = frozenset({"item_id", "name", "description", "quantity", "rate", "tax_id",
                            "discount", "unit", "item_order"})
CONTACT_PERSON_KEYS = frozenset({
    "contact_person_id", "salutation", "first_name", "last_name", "email", "phone", "mobile",
    "designation", "department", "skype", "is_primary_contact"})
_BOOKS_NESTED = {"contacts": {"contact_persons": (CONTACT_PERSON_KEYS, MAX_CONTACT_PERSONS)},
                 "estimates": {"line_items": (LINE_ITEM_KEYS, MAX_LINE_ITEMS)},
                 "items": {}}
_BOOKS_FRAGMENTS = ("portal", "remind", "statement", "invite", "send", "approve", "submit",
                    "to_mail", "cc_mail")


def _books_key(key: Any, kind: str) -> str:
    if not isinstance(key, str) or _BOOKS_KEY_RE.fullmatch(key) is None:
        raise ZohoRequestError("field names must match [a-z][a-z0-9_]{0,63}")
    # contact persons legitimately carry an `email` address; storing one never sends anything
    mail_ok = kind == "contacts" and key == "email"
    if any(part in key for part in _BOOKS_FRAGMENTS) or ("mail" in key and not mail_ok):
        raise ZohoRequestError(f"field not allowed: {key}")
    return key


def _books_entries(key: str, kind: str, value: Any, allowed: frozenset, limit: int) -> list:
    if not isinstance(value, list) or len(value) > limit or not all(
            isinstance(entry, dict) for entry in value):
        raise ZohoRequestError(f"{key} must be a list of at most {limit} objects")
    out = []
    for entry in value:
        for sub, sub_value in entry.items():
            _books_key(sub, kind)
            if sub not in allowed or not _scalar_ok(sub_value):
                raise ZohoRequestError(f"{key} entry field not allowed: {sub}")
        out.append(dict(entry))
    return out


def validate_books_fields(fields: Any, kind: str) -> dict:
    """Validated copy of caller-supplied Books fields for contacts, estimates or items."""
    nested = _BOOKS_NESTED[kind]
    out: dict[str, Any] = {}
    for key, value in _check_shape(fields).items():
        _books_key(key, kind)
        if key in nested:
            allowed, limit = nested[key]
            out[key] = _books_entries(key, kind, value, allowed, limit)
        else:
            out[key] = _clean_value(key, value)
    return out


def _books_body_rule(kind: str):
    def rule(body: Any, _module: str | None = None) -> None:
        validate_books_fields(body, kind)
    return rule


validate_contact_body = _books_body_rule("contacts")
validate_estimate_body = _books_body_rule("estimates")
validate_item_body = _books_body_rule("items")
