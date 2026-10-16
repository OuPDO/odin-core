"""Request validation: fixed endpoint allowlist with per-endpoint query rules.

Everything is checked before any network I/O: method, anchored path pattern, the exact
set of query keys, mandatory keys, value formats and bounds. Callers (crm.py, books.py)
validate too, but this module is the authority; a buggy caller cannot get past it.
"""
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from integrations.zoho.errors import ZohoRequestError
from integrations.zoho.write_rules import (
    check_denied,
    validate_contact_body,
    validate_estimate_body,
    validate_item_body,
    validate_note_body,
    validate_record_body,
    validate_task_body,
)

PER_PAGE = 50
CRM_MAX_PAGE = 40
BOOKS_MAX_PAGE = 100
CRM_MIN_TEXT = 2
CRM_MAX_TEXT = 200
BOOKS_MAX_TEXT = 100
NOTES_FIELDS = "Note_Title,Note_Content,Created_Time,Modified_Time,Owner"
ESTIMATE_STATUSES = ("draft", "sent", "invoiced", "accepted", "declined", "expired")
MARKABLE_STATUSES = ("sent", "accepted", "declined")  # POST .../status/<x>: marks only, no mail

# Callers cannot choose CRM fields: every request carries exactly this list for its module,
# and responses are projected to it (see crm.py). Names are standard Zoho API names.
MODULE_FIELDS = {
    "Leads": ("First_Name,Last_Name,Full_Name,Company,Email,Phone,Mobile,Designation,"
              "Lead_Status,Lead_Source,Owner,Created_Time,Modified_Time,Website,City"),
    "Contacts": ("First_Name,Last_Name,Full_Name,Account_Name,Email,Phone,Mobile,Title,"
                 "Lead_Source,Owner,Created_Time,Modified_Time,Mailing_City"),
    "Deals": "Deal_Name,Stage,Amount,Closing_Date,Account_Name,Contact_Name,Owner",
    "Accounts": "Account_Name,Website,Phone,Billing_City,Owner,Created_Time,Modified_Time",
}
# Search by field is fixed per module.
MODULE_CRITERIA = {
    "Leads": ("Full_Name", "Company"),
    "Contacts": ("Full_Name", "Account_Name"),
    "Accounts": ("Account_Name",),
    "Deals": ("Deal_Name",),
}
_ALL_CRITERIA_FIELDS = ("Full_Name", "Company", "Account_Name", "Deal_Name")

_ID_RE = re.compile(r"[0-9]{1,25}")
_CRITERIA_RE = re.compile(
    rf"\(({'|'.join(_ALL_CRITERIA_FIELDS)}):equals:((?:\\[\\(),]|[^\\(),])+)\)")
_UNESCAPE_RE = re.compile(r"\\([\\(),])")
_MODULES = "(?P<module>Leads|Contacts|Deals|Accounts)"
_ID = "[0-9]{1,25}"

Validator = Callable[[Any], Any]
ModuleValidator = Callable[[str, Any], Any]


def is_id(value: Any) -> bool:
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


def validate_id(value: Any, what: str = "id") -> str:
    if not is_id(value):
        raise ZohoRequestError(f"{what} must be 1 to 25 digits")
    return value


def validate_page(value: Any, max_page: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= max_page:
        raise ZohoRequestError(f"page must be an integer from 1 to {max_page}")
    return value


def validate_per_page(value: Any) -> int:
    if isinstance(value, bool) or value != PER_PAGE or not isinstance(value, int):
        raise ZohoRequestError(f"per_page must be {PER_PAGE}")
    return value


def validate_text(value: Any, what: str, min_len: int, max_len: int) -> str:
    """Printable text of min_len..max_len characters (surrounding blanks do not count)."""
    ok = isinstance(value, str) and min_len <= len(value.strip()) and len(value) <= max_len
    if not ok or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ZohoRequestError(
            f"{what} must be {min_len} to {max_len} printable characters")
    return value



def validate_notes_fields(value: Any) -> str:
    if value != NOTES_FIELDS:
        raise ZohoRequestError("fields for notes are fixed")
    return value


def validate_criteria(value: Any) -> str:
    """Only `(<fixed field>:equals:<escaped text>)` with 2..200 characters of text."""
    match = _CRITERIA_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ZohoRequestError("criteria must be (Field:equals:escaped value) for a known field")
    validate_text(_UNESCAPE_RE.sub(r"\1", match.group(2)), "criteria value",
                  CRM_MIN_TEXT, CRM_MAX_TEXT)
    return value


def module_fields(module: str, value: Any) -> str:
    if value != MODULE_FIELDS[module]:
        raise ZohoRequestError(f"fields for {module} are fixed")
    return value


def module_criteria(module: str, value: Any) -> str:
    match = _CRITERIA_RE.fullmatch(value)  # value already passed validate_criteria
    if match is None or match.group(1) not in MODULE_CRITERIA[module]:
        raise ZohoRequestError(f"criteria field not allowed for {module}")
    return value


def validate_status(value: Any) -> str:
    if value not in ESTIMATE_STATUSES:
        raise ZohoRequestError(f"status must be one of {', '.join(ESTIMATE_STATUSES)}")
    return value


def _customer_id(value: Any) -> str:
    return validate_id(value, "customer_id")


@dataclass(frozen=True)
class Endpoint:
    pattern: re.Pattern
    rules: Mapping[str, Validator]
    required: frozenset[str] = frozenset()
    exactly_one_of: frozenset[str] = frozenset()
    books: bool = False
    module_rules: Mapping[str, ModuleValidator] | None = None  # bound to the path's module
    method: str = "GET"
    body_rule: ModuleValidator | None = None  # None: the request carries no body


_crm_text = partial(validate_text, what="search value", min_len=CRM_MIN_TEXT,
                    max_len=CRM_MAX_TEXT)
_CRM_PAGING = {"page": partial(validate_page, max_page=CRM_MAX_PAGE),
               "per_page": validate_per_page}
_BOOKS_PAGING = {"page": partial(validate_page, max_page=BOOKS_MAX_PAGE),
                 "per_page": validate_per_page}
_SEARCH_KEYS = frozenset({"word", "email", "phone", "criteria"})
_PAGING_KEYS = frozenset({"page", "per_page"})


def _ep(pattern: str, rules: Mapping[str, Validator], required: tuple = (),
        one_of: frozenset = frozenset(), books: bool = False,
        module_rules: Mapping[str, ModuleValidator] | None = None, method: str = "GET",
        body_rule: ModuleValidator | None = None) -> Endpoint:
    return Endpoint(re.compile(pattern), rules, frozenset(required), one_of, books,
                    module_rules, method, body_rule)


def _write(method: str, pattern: str, body_rule: ModuleValidator | None,
           books: bool = False) -> Endpoint:
    """Write endpoint: no query parameters at all, a body that must pass `body_rule`."""
    return _ep(pattern, {}, books=books, method=method, body_rule=body_rule)


_ANY = str  # the module-bound rule does the real check; the generic rule only types the value

ALLOWED_ENDPOINTS = (
    _ep(rf"/crm/v6/{_MODULES}/search",
        {"word": _crm_text, "email": _crm_text, "phone": _crm_text,
         "criteria": validate_criteria, **_CRM_PAGING},
        required=_PAGING_KEYS, one_of=_SEARCH_KEYS,
        module_rules={"criteria": module_criteria}),
    _ep(rf"/crm/v6/{_MODULES}",
        {"fields": _ANY, **_CRM_PAGING}, required={"fields"} | _PAGING_KEYS,
        module_rules={"fields": module_fields}),
    _ep(rf"/crm/v6/{_MODULES}/{_ID}", {"fields": _ANY}, required=("fields",),
        module_rules={"fields": module_fields}),
    _ep(rf"/crm/v6/{_MODULES}/{_ID}/Notes",
        {"fields": validate_notes_fields, **_CRM_PAGING}, required={"fields"} | _PAGING_KEYS),
    _ep("/books/v3/contacts",
        {"search_text": partial(validate_text, what="search_text", min_len=1,
                                max_len=BOOKS_MAX_TEXT), **_BOOKS_PAGING},
        required={"search_text"} | _PAGING_KEYS, books=True),
    _ep(rf"/books/v3/contacts/{_ID}", {}, books=True),
    _ep("/books/v3/estimates",
        {"status": validate_status, "customer_id": _customer_id, **_BOOKS_PAGING},
        required=_PAGING_KEYS, books=True),
    _ep(rf"/books/v3/estimates/{_ID}", {}, books=True),
    _ep("/books/v3/items", dict(_BOOKS_PAGING), required=_PAGING_KEYS, books=True),
    _ep("/books/v3/settings/taxes", {}, books=True),
    # ---- writes: exact method, path and body shape; never mail, delete or invoices ----
    _write("POST", rf"/crm/v6/{_MODULES}", validate_record_body),
    _write("PUT", rf"/crm/v6/{_MODULES}/{_ID}", validate_record_body),
    _write("POST", rf"/crm/v6/{_MODULES}/{_ID}/Notes", validate_note_body),
    _write("POST", "/crm/v6/Tasks", validate_task_body),
    _write("POST", "/books/v3/contacts", validate_contact_body, books=True),
    _write("PUT", rf"/books/v3/contacts/{_ID}", validate_contact_body, books=True),
    _write("POST", "/books/v3/estimates", validate_estimate_body, books=True),
    _write("PUT", rf"/books/v3/estimates/{_ID}", validate_estimate_body, books=True),
    _write("POST", rf"/books/v3/estimates/{_ID}/status/({'|'.join(MARKABLE_STATUSES)})", None,
           books=True),
    _write("POST", "/books/v3/items", validate_item_body, books=True),
)


def validate_request(method: str, path: str, params: Mapping[str, Any],
                     body: Any = None) -> Endpoint:
    """Raise ZohoRequestError unless (method, path, query, body) is allowed; return the endpoint."""
    if method not in ("GET", "POST", "PUT"):
        raise ZohoRequestError("method not allowed")
    if not isinstance(path, str):
        raise ZohoRequestError("path not allowed")
    check_denied(method, path, params)
    for endpoint in ALLOWED_ENDPOINTS:
        match = endpoint.pattern.fullmatch(path)
        if match and endpoint.method == method:
            _validate_query(endpoint, params, match.groupdict().get("module"))
            _validate_body(endpoint, body, match.groupdict().get("module"))
            return endpoint
    raise ZohoRequestError("path not allowed")


def _validate_body(endpoint: Endpoint, body: Any, module: str | None) -> None:
    if endpoint.body_rule is None:
        if body is not None:
            raise ZohoRequestError("this request takes no body")
        return
    endpoint.body_rule(body, module)


def _validate_query(endpoint: Endpoint, params: Mapping[str, Any], module: str | None) -> None:
    for key, value in params.items():
        rule = endpoint.rules.get(key)
        if rule is None:
            raise ZohoRequestError(f"query parameter not allowed: {key}")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ZohoRequestError(f"query parameter has invalid type: {key}")
        rule(value)
        bound = (endpoint.module_rules or {}).get(key)
        if bound and module:
            bound(module, value)
    missing = endpoint.required - params.keys()
    if missing:
        raise ZohoRequestError(f"missing query parameter: {min(missing)}")
    if endpoint.exactly_one_of and len(endpoint.exactly_one_of & params.keys()) != 1:
        raise ZohoRequestError(
            f"exactly one of {', '.join(sorted(endpoint.exactly_one_of))} is required")
