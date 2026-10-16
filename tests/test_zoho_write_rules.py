"""Write rules: field validation, mail denylist, body shapes. Pure functions, no network."""
import math

import pytest

from integrations.zoho import validation
from integrations.zoho.client import ZohoRequestError, validate_request
from integrations.zoho.write_rules import (
    crm_record_body,
    validate_books_fields,
    validate_crm_fields,
)

REC_BODY_KEYS = {"data", "trigger", "skip_feature_execution"}


# ---- CRM field rules -------------------------------------------------------------------------

def test_crm_fields_accept_documented_value_types():
    fields = {"Last_Name": "Muster", "Annual_Revenue": 5, "Rating": 4.5, "Email_Opt_Out": True,
              "Description": "a\nb\tc", "Fax": None, "Hobbies": ["a", "b"],
              "Account_Name": {"id": "123"}, "Parent_Id": {"id": "9"}, "Lead_Name": {"id": "1"}}
    assert validate_crm_fields(fields) == fields


@pytest.mark.parametrize("key", [
    "trigger", "Trigger", "lar_id", "LAR_ID", "Owner", "owner", "Created_By", "Modified_By",
    "Tag", "Remind_At", "Reminder", "Call_Reminder", "Remind_Participants", "$se_module",
    "$approval", "Layout", "process", "Approval", "Send_Notification", "Notify",
    "skip_feature_execution", "wf_trigger", "id", "Send_Email_Copy",
])
def test_crm_forbidden_keys_rejected(key):
    with pytest.raises(ZohoRequestError):
        validate_crm_fields({"Last_Name": "x", key: "y"})


@pytest.mark.parametrize("key", ["1abc", "a-b", "a b", "", "Ä", "a" * 65, "Last.Name", "_x"])
def test_crm_bad_key_syntax_rejected(key):
    with pytest.raises(ZohoRequestError):
        validate_crm_fields({key: "x"})


@pytest.mark.parametrize("fields", [
    {f"F{i}": "x" for i in range(61)},
    {"A": "x" * 5001},
    {"A": "bad\x00char"}, {"A": "bell\x07"},
    {"A": float("nan")}, {"A": math.inf},
    {"A": [1, 2]}, {"A": ["x"] * 51}, {"A": ["x" * 5001]}, {"A": [["x"]]},
    {"A": {"x": 1}}, {"Note": {"id": "1"}}, {"Account_Name": {"id": "abc"}},
    {"Account_Name": {"id": "1", "name": "x"}}, {"Account_Name": {"id": 1}},
    {"Account_Name": {}}, {"A": (1, 2)}, {"A": b"x"}, {"A": object()},
])
def test_crm_bad_values_rejected(fields):
    with pytest.raises(ZohoRequestError):
        validate_crm_fields(fields)


@pytest.mark.parametrize("fields", [None, [], "x", 5, {}])
def test_crm_fields_must_be_non_empty_dict(fields):
    with pytest.raises(ZohoRequestError):
        validate_crm_fields(fields)


def test_crm_non_string_key_rejected():
    with pytest.raises(ZohoRequestError):
        validate_crm_fields({1: "x"})


def test_crm_sixty_keys_are_fine():
    fields = {f"F{i}": "x" for i in range(60)}
    assert validate_crm_fields(fields) == fields


def test_validated_fields_are_a_copy():
    fields = {"A": ["x"]}
    out = validate_crm_fields(fields)
    out["A"].append("y")
    assert fields == {"A": ["x"]}


# ---- record body -----------------------------------------------------------------------------

def test_record_body_shape_is_fixed():
    body = crm_record_body({"Last_Name": "x"})
    assert body == {"data": [{"Last_Name": "x"}], "trigger": [],
                    "skip_feature_execution": [{"name": "cadences"}]}


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(trigger=["workflow"]),
    lambda b: b.update(trigger=None),
    lambda b: b.update(skip_feature_execution=[]),
    lambda b: b.update(skip_feature_execution=[{"name": "layout_rules"}]),
    lambda b: b.update(lar_id="1"),
    lambda b: b.update(process=[]),
    lambda b: b.update(wf_trigger="true"),
    lambda b: b.update(data=[]),
    lambda b: b.update(data=[{"A": "x"}, {"A": "y"}]),
    lambda b: b.update(data={"A": "x"}),
    lambda b: b.update(data=[{"Owner": {"id": "1"}}]),
    lambda b: b.update(data=[{"A": "x", "Remind_At": "2026-01-01"}]),
    lambda b: b.pop("trigger"),
])
def test_validator_rejects_tampered_record_bodies(mutate):
    body = crm_record_body({"Last_Name": "x"})
    mutate(body)
    with pytest.raises(ZohoRequestError):
        validate_request("POST", "/crm/v6/Leads", {}, body)


def test_validator_accepts_exact_record_body():
    validate_request("POST", "/crm/v6/Leads", {}, crm_record_body({"Last_Name": "x"}))
    validate_request("PUT", "/crm/v6/Deals/7", {}, crm_record_body({"Stage": "Won"}))


def test_post_body_must_be_a_dict_and_present():
    for body in (None, [], "x"):
        with pytest.raises(ZohoRequestError):
            validate_request("POST", "/crm/v6/Leads", {}, body)


def test_read_endpoints_reject_a_body():
    with pytest.raises(ZohoRequestError):
        validate_request("GET", "/crm/v6/Leads/1", {"fields": validation.MODULE_FIELDS["Leads"]},
                         {"x": 1})


def test_write_methods_and_paths_are_exact():
    body = crm_record_body({"Last_Name": "x"})
    for method, path in [("DELETE", "/crm/v6/Leads/1"), ("PATCH", "/crm/v6/Leads/1"),
                         ("POST", "/crm/v6/Leads/1"), ("PUT", "/crm/v6/Leads"),
                         ("POST", "/crm/v6/Users"), ("POST", "/crm/v6/Leads/search"),
                         ("PUT", "/crm/v6/Leads/1/Notes"), ("POST", "/crm/v6/Leads/1/Notes/")]:
        with pytest.raises(ZohoRequestError):
            validate_request(method, path, {}, body)


def test_write_requests_take_no_query():
    with pytest.raises(ZohoRequestError):
        validate_request("POST", "/crm/v6/Leads", {"trigger": "x"},
                         crm_record_body({"Last_Name": "x"}))


# ---- mail denylist ---------------------------------------------------------------------------

DENIED_SEGMENTS = ["email", "emails", "send_mail", "sendmail", "mail", "remind", "reminder",
                   "paymentreminder", "statements", "portal", "enable_portal", "invite",
                   "comments", "send", "approve", "submit", "EMAIL", "Send_Mail"]


@pytest.mark.parametrize("segment", DENIED_SEGMENTS)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
def test_denylist_rejects_every_mail_segment_in_every_position(segment, method):
    body = {"x": 1} if method != "GET" else None
    for path in (f"/books/v3/estimates/1/{segment}", f"/crm/v6/Leads/1/actions/{segment}",
                 f"/books/v3/{segment}"):
        with pytest.raises(ZohoRequestError, match="not allowed"):
            validate_request(method, path, {}, body)


@pytest.mark.parametrize("path", [
    "/crm/v6/Leads/1/actions/send_mail", "/books/v3/estimates/1/email",
    "/books/v3/invoices/1/email", "/books/v3/contacts/1/portal/enable",
    "/books/v3/contacts/1/paymentreminder/disable", "/books/v3/contacts/1/statements/email",
    "/books/v3/estimates/1/comments", "/books/v3/estimates/1/submit",
    "/books/v3/estimates/1/approve", "/crm/v6/Leads/1/actions/send",
])
def test_mail_routes_rejected(path):
    with pytest.raises(ZohoRequestError):
        validate_request("POST", path, {}, {})


@pytest.mark.parametrize("key", ["send", "send_email", "send_mail", "SEND"])
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/books/v3/estimates", None),
    ("POST", "/books/v3/estimates", {"customer_id": "1"}),
    ("PUT", "/books/v3/estimates/1", {"customer_id": "1"}),
])
def test_denylist_rejects_send_query_keys(key, method, path, body):
    with pytest.raises(ZohoRequestError):
        validate_request(method, path, {key: "true"}, body)


@pytest.mark.parametrize("key", ["email", "remind", "portal", "invite", "approve", "submit",
                                 "statements", "comments", "mail"])
def test_write_requests_reject_the_full_denylist_as_query_keys(key):
    with pytest.raises(ZohoRequestError):
        validate_request("POST", "/books/v3/estimates", {key: "1"}, {"customer_id": "1"})


def test_denylist_holds_even_if_the_allowlist_were_widened(monkeypatch):
    widened = validation._ep(r"/books/v3/estimates/[0-9]{1,25}/email", {}, books=True)
    monkeypatch.setattr(validation, "ALLOWED_ENDPOINTS",
                        validation.ALLOWED_ENDPOINTS + (widened,))
    with pytest.raises(ZohoRequestError, match="mail"):
        validate_request("GET", "/books/v3/estimates/1/email", {})


def test_crm_search_by_email_still_works():
    validate_request("GET", "/crm/v6/Leads/search",
                     {"email": "a@b.de", "page": 1, "per_page": 50})


# ---- Books field rules -----------------------------------------------------------------------

def test_books_fields_accept_line_items_and_contact_persons():
    est = {"customer_id": "1", "date": "2026-10-16", "line_items": [
        {"item_id": "5", "name": "x", "description": "d", "quantity": 2, "rate": 10.5,
         "tax_id": "3", "discount": "10%", "unit": "h", "item_order": 1}]}
    assert validate_books_fields(est, "estimates") == est
    contact = {"contact_name": "X", "contact_persons": [
        {"first_name": "A", "last_name": "B", "email": "a@b.de", "is_primary_contact": True}]}
    assert validate_books_fields(contact, "contacts") == contact
    assert validate_books_fields({"name": "Beratung", "rate": 100}, "items")


@pytest.mark.parametrize("kind,fields", [
    ("contacts", {"contact_name": "X", "is_portal_enabled": True}),
    ("contacts", {"contact_name": "X", "portal_status": "x"}),
    ("contacts", {"contact_name": "X", "contact_persons": [{"enable_portal": True}]}),
    ("contacts", {"contact_name": "X", "contact_persons": [{"is_portal_enabled": True}]}),
    ("contacts", {"contact_name": "X", "contact_persons": [{"first_name": "A", "portal": 1}]}),
    ("contacts", {"contact_name": "X", "payment_reminder_enabled": True}),
    ("contacts", {"contact_name": "X", "send": True}),
    ("contacts", {"contact_name": "X", "contact_persons": "x"}),
    ("contacts", {"contact_name": "X", "contact_persons": ["x"]}),
    ("contacts", {"contact_name": "X", "contact_persons": [{"x": {"y": 1}}]}),
    ("contacts", {"contact_name": "X", "contact_persons": [{"unknown_key": "x"}]}),
    ("contacts", {"contact_name": "X", "contact_persons": [{}] * 21}),
    ("estimates", {"customer_id": "1", "send": True}),
    ("estimates", {"customer_id": "1", "email": "a@b.de"}),
    ("estimates", {"customer_id": "1", "to_mail_ids": ["a@b.de"]}),
    ("estimates", {"customer_id": "1", "cc_mail_ids": ["a@b.de"]}),
    ("estimates", {"customer_id": "1", "mail_to": "a"}),
    ("estimates", {"customer_id": "1", "line_items": [{"name": "x", "email": "a"}]}),
    ("estimates", {"customer_id": "1", "line_items": [{"bogus": 1}]}),
    ("estimates", {"customer_id": "1", "line_items": [{"name": ["x"]}]}),
    ("estimates", {"customer_id": "1", "line_items": "x"}),
    ("estimates", {"customer_id": "1", "line_items": [{"name": "x"}] * 101}),
    ("estimates", {"customer_id": "1", "custom_fields": [{"a": 1}]}),
    ("estimates", {"customer_id": "1", "billing": {"a": 1}}),
    ("items", {"name": "x", "approve": True}),
    ("items", {"name": "x", "Name": "x"}),
    ("items", {"1name": "x"}),
])
def test_books_bad_fields_rejected(kind, fields):
    with pytest.raises(ZohoRequestError):
        validate_books_fields(fields, kind)


@pytest.mark.parametrize("fields", [None, {}, [], "x"])
def test_books_fields_must_be_non_empty_dict(fields):
    with pytest.raises(ZohoRequestError):
        validate_books_fields(fields, "items")


def test_validator_rejects_estimate_status_outside_enum():
    for state in ("draft", "invoiced", "expired", "accepted", "SENT", "sent%2F", "send"):
        with pytest.raises(ZohoRequestError):
            validate_request("POST", f"/books/v3/estimates/1/status/{state}", {})


@pytest.mark.parametrize("state", ["sent", "declined"])
def test_mark_status_endpoints_exist_and_take_no_body(state):
    validate_request("POST", f"/books/v3/estimates/1/status/{state}", {})
    with pytest.raises(ZohoRequestError):
        validate_request("POST", f"/books/v3/estimates/1/status/{state}", {}, {"x": 1})


def test_no_delete_and_no_invoice_endpoints():
    for method in ("DELETE", "PATCH"):
        for path in ("/books/v3/contacts/1", "/books/v3/estimates/1", "/crm/v6/Leads/1"):
            with pytest.raises(ZohoRequestError):
                validate_request(method, path, {})
    for path in ("/books/v3/invoices", "/books/v3/customerpayments", "/books/v3/recurringinvoices",
                 "/books/v3/invoices/1", "/books/v3/items/1"):
        for method in ("GET", "POST", "PUT"):
            with pytest.raises(ZohoRequestError):
                validate_request(method, path, {}, {} if method != "GET" else None)


# ---- tampered task / note bodies, handed straight to the validator ---------------------------

def _task(**extra):
    return {"data": [{"Subject": "S", "Due_Date": "2026-11-02", **extra}], "trigger": []}


@pytest.mark.parametrize("body", [
    {"data": [{"Subject": "S", "Due_Date": "2026-11-02"}]},  # trigger missing
    {"data": [{"Subject": "S", "Due_Date": "2026-11-02"}], "trigger": ["workflow"]},
    _task(Remind_At="2026-11-01"), _task(Reminder=True), _task(Owner={"id": "1"}),
    _task(Who_Id="5"), _task(Who_Id={"id": "x"}), _task(Who_Id={"id": "1"}, What_Id={"id": "2"}),
    _task(Who_Id={"id": "1"}, **{"$se_module": "Leads"}),
    _task(What_Id={"id": "1"}), _task(**{"$se_module": "Leads"}),
    _task(What_Id={"id": "1"}, **{"$se_module": "Users"}),
    {"data": [{"Due_Date": "2026-11-02"}], "trigger": []},
    {"data": "x", "trigger": []},
])
def test_validator_rejects_tampered_task_bodies(body):
    with pytest.raises(ZohoRequestError):
        validate_request("POST", "/crm/v6/Tasks", {}, body)


def test_validator_accepts_exact_task_body():
    validate_request("POST", "/crm/v6/Tasks", {}, _task(What_Id={"id": "1"},
                                                        **{"$se_module": "Deals"}))


@pytest.mark.parametrize("body", [
    {"data": [{"Note_Title": "t"}]},
    {"data": [{"Note_Title": "t", "Note_Content": "c", "Parent_Id": "1"}]},
    {"data": [{"Note_Title": "t", "Note_Content": "c"}], "trigger": []},
    {"data": [{"Note_Title": "", "Note_Content": "c"}]},
])
def test_validator_rejects_tampered_note_bodies(body):
    with pytest.raises(ZohoRequestError):
        validate_request("POST", "/crm/v6/Leads/1/Notes", {}, body)


def test_non_string_path_and_unknown_method_rejected():
    for method, path in (("POST", None), ("POST", 5), ("DELETE", "/x"), ("post", "/x")):
        with pytest.raises(ZohoRequestError):
            validate_request(method, path, {}, {})


def test_unknown_path_with_valid_method_rejected():
    with pytest.raises(ZohoRequestError, match="path not allowed"):
        validate_request("POST", "/crm/v6/Leads/1/Attachments", {}, {})
