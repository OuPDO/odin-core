"""CRM write operations against mocked Zoho: exact method, path, query and JSON body."""
import httpx
import pytest
import respx
from zoho_support import API, ENV_BOTH, SECRETS, TokenMock, data_calls

from integrations.zoho import writes
from integrations.zoho.client import (
    ZohoAPIError,
    ZohoClient,
    ZohoConfig,
    ZohoOutcomeUnknown,
    ZohoRequestError,
)

RECORD_EXTRAS = {"trigger": [], "skip_feature_execution": [{"name": "cadences"}]}


def ok(record_id="111", code="SUCCESS", status="success", http=201, **details):
    return httpx.Response(http, json={"data": [{
        "code": code, "status": status, "message": "record added",
        "details": {"id": record_id, "Created_Time": "x", "Created_By": {"id": "9"}, **details}}]})


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client():
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


def body_of(route) -> dict:
    import json
    return json.loads(route.calls.last.request.content)


# ---- happy paths -----------------------------------------------------------------------------

@pytest.mark.parametrize("org", ["om", "ado"])
@pytest.mark.parametrize("module", ["Leads", "Contacts", "Deals", "Accounts"])
async def test_create_posts_exact_body(org, module, client, router):
    TokenMock(router)
    fields = {"Last_Name": "Muster", "Email": "e@example.org", "Email_Opt_Out": False}
    route = router.post(f"{API}/crm/v6/{module}", json={
        "data": [fields], **RECORD_EXTRAS}).mock(return_value=ok())
    result = await writes.create(client, org, module, fields)
    assert route.call_count == 1
    assert result == {"ok": True,
                      "results": [{"id": "111", "status": "success", "code": "SUCCESS"}]}
    assert str(route.calls.last.request.url) == f"{API}/crm/v6/{module}"


async def test_update_puts_exact_body(client, router):
    TokenMock(router)
    route = router.put(f"{API}/crm/v6/Deals/77", json={
        "data": [{"Stage": "Closed Won", "Amount": 100}], **RECORD_EXTRAS}).mock(
        return_value=ok("77", http=200))
    result = await writes.update(client, "ado", "Deals", "77", {"Stage": "Closed Won",
                                                                "Amount": 100})
    assert route.call_count == 1
    assert result["results"] == [{"id": "77", "status": "success", "code": "SUCCESS"}]


async def test_add_note_body_has_no_trigger(client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Accounts/5/Notes", json={
        "data": [{"Note_Title": "Anruf", "Note_Content": "Zeile 1\nZeile 2"}]}).mock(
        return_value=ok("900"))
    result = await writes.add_note(client, "om", "Accounts", "5", "Anruf", "Zeile 1\nZeile 2")
    assert route.call_count == 1
    assert result["results"][0]["id"] == "900"
    assert set(body_of(route)) == {"data"}


@pytest.mark.parametrize("related,expected", [
    (None, {}),
    (("Contacts", "42"), {"Who_Id": {"id": "42"}}),
    (("Leads", "42"), {"What_Id": {"id": "42"}, "$se_module": "Leads"}),
    (("Deals", "42"), {"What_Id": {"id": "42"}, "$se_module": "Deals"}),
    (("Accounts", "42"), {"What_Id": {"id": "42"}, "$se_module": "Accounts"}),
])
async def test_create_task_body(related, expected, client, router):
    TokenMock(router)
    record = {"Subject": "Rueckruf", "Due_Date": "2026-11-02", "Description": "Angebot",
              **expected}
    route = router.post(f"{API}/crm/v6/Tasks", json={"data": [record], "trigger": []}).mock(
        return_value=ok("55"))
    module, rid = related or (None, None)
    result = await writes.create_task(client, "om", "Rueckruf", "2026-11-02", module, rid,
                                      "Angebot")
    assert route.call_count == 1
    assert result["results"][0]["id"] == "55"
    sent = body_of(route)
    assert set(sent) == {"data", "trigger"} and sent["trigger"] == []
    assert not any("remind" in key.lower() for key in sent["data"][0])


async def test_create_task_without_description(client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Tasks", json={
        "data": [{"Subject": "S1", "Due_Date": "2026-11-02"}], "trigger": []}).mock(
        return_value=ok())
    await writes.create_task(client, "om", "S1", "2026-11-02")
    assert route.call_count == 1


# ---- response projection ---------------------------------------------------------------------

async def test_result_is_projected_never_the_full_body(client, router):
    TokenMock(router)
    router.post(f"{API}/crm/v6/Leads").mock(return_value=httpx.Response(201, json={"data": [{
        "code": "SUCCESS", "status": "success", "message": "SECRET-MESSAGE",
        "details": {"id": "1", "Created_By": {"name": "Hans", "email": "h@example.org"},
                    "$editable": True}}]}))
    result = await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert "SECRET-MESSAGE" not in str(result) and "Hans" not in str(result)
    assert result == {"ok": True,
                      "results": [{"id": "1", "status": "success", "code": "SUCCESS"}]}


async def test_per_record_error_is_reported_with_safe_code_and_field(client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(return_value=httpx.Response(400, json={"data": [{
        "code": "DUPLICATE_DATA", "status": "error", "message": "duplicate",
        "details": {"api_name": "Email", "id": "5", "duplicate_record": {"id": "5"}}}]}))
    result = await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert result == {"ok": False, "results": [
        {"id": "5", "status": "error", "code": "DUPLICATE_DATA", "field": "Email"}]}
    assert route.call_count == 1  # a confirmed rejection is never retried


async def test_unknown_upstream_values_are_neutralised(client, router):
    TokenMock(router)
    router.post(f"{API}/crm/v6/Leads").mock(return_value=httpx.Response(201, json={"data": [
        {"code": "whatever<script>", "status": "weird",
         "details": {"id": "not-digits", "api_name": "bad key!"}}]}))
    result = await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert result == {"ok": False, "results": [
        {"id": None, "status": "unknown", "code": "upstream_error"}]}


async def test_http_error_becomes_sanitized_api_error(client, router):
    TokenMock(router)
    router.post(f"{API}/crm/v6/Leads").mock(return_value=httpx.Response(400, json={
        "code": "INVALID_DATA", "message": "SECRET-DETAIL", "status": "error"}))
    with pytest.raises(ZohoAPIError, match="HTTP 400, INVALID_DATA") as exc:
        await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert "SECRET-DETAIL" not in str(exc.value)


@pytest.mark.parametrize("response", [
    httpx.Response(201, json={"data": "x"}), httpx.Response(201, json={"data": ["x"]}),
    httpx.Response(201, json={}), httpx.Response(201, json={"data": []}),
    httpx.Response(201, text="<html>"), httpx.Response(200, json=[1]), httpx.Response(204),
    httpx.Response(500, json={"code": "INTERNAL_ERROR"}), httpx.Response(502),
    httpx.Response(503, json={"data": [{"code": "SUCCESS", "status": "success"}]}),
    httpx.Response(307, headers={"location": "https://evil.example/x"}),
])
async def test_unclear_result_after_dispatch_is_outcome_unknown(response, client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(return_value=response)
    with pytest.raises(ZohoOutcomeUnknown, match="outcome_unknown: Ergebnis unbekannt"):
        await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert route.call_count == 1


@pytest.mark.parametrize("response", [
    httpx.Response(400, json={"code": "INVALID_DATA"}),  # no record envelope
    httpx.Response(400, text="x"), httpx.Response(403, json={"code": "NO_PERMISSION"}),
    httpx.Response(404, json={}), httpx.Response(429, json={"code": "TOO_MANY_REQUESTS"}),
])
async def test_other_4xx_is_a_confirmed_rejection_not_unknown(response, client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(return_value=response)
    with pytest.raises(ZohoAPIError, match="HTTP 4") as exc:
        await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert not isinstance(exc.value, ZohoOutcomeUnknown)
    assert route.call_count == 1


async def test_http_400_envelope_through_the_client_is_sanitized(client, router):
    TokenMock(router)
    router.post(f"{API}/crm/v6/Leads").mock(return_value=httpx.Response(400, json={
        "data": [{"code": "UNKNOWN_<b>", "status": "error", "message": "SECRET",
                  "details": {"api_name": "Email", "id": "5", "$x": 1, "duplicate_record": {
                      "Owner": {"name": "Hans"}}}}]}))
    result = await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert result == {"ok": False, "results": [
        {"id": "5", "status": "error", "code": "upstream_error", "field": "Email"}]}
    assert "SECRET" not in str(result) and "Hans" not in str(result)


# ---- rejections with zero network ------------------------------------------------------------

OVERRIDES = ["trigger", "lar_id", "Owner", "$se_module", "$approval", "Remind_At", "Reminder",
             "skip_feature_execution", "wf_trigger", "process", "Approval", "Tag", "Layout",
             "Send_Notification", "Notify", "Created_By", "Modified_By"]


@pytest.mark.parametrize("key", OVERRIDES)
async def test_caller_overrides_rejected_with_zero_network(key, client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await writes.create(client, "om", "Leads", {"Last_Name": "x", key: []})
    with pytest.raises(ZohoRequestError):
        await writes.update(client, "om", "Leads", "1", {key: "x"})
    assert token.calls == 0 and not data_calls(router)


@pytest.mark.parametrize("call", [
    lambda c: writes.create(c, "om", "Users", {"A": "x"}),
    lambda c: writes.create(c, "om", "leads", {"A": "x"}),
    lambda c: writes.create(c, "evil", "Leads", {"A": "x"}),
    lambda c: writes.create(c, "om", "Leads", {}),
    lambda c: writes.update(c, "om", "Leads", "1/../2", {"A": "x"}),
    lambda c: writes.update(c, "om", "Leads", "abc", {"A": "x"}),
    lambda c: writes.update(c, "om", "Notes", "1", {"A": "x"}),
    lambda c: writes.add_note(c, "om", "Leads", "x", "t", "c"),
    lambda c: writes.add_note(c, "om", "Users", "1", "t", "c"),
    lambda c: writes.add_note(c, "om", "Leads", "1", "", "c"),
    lambda c: writes.add_note(c, "om", "Leads", "1", "t", ""),
    lambda c: writes.add_note(c, "om", "Leads", "1", "t" * 201, "c"),
    lambda c: writes.add_note(c, "om", "Leads", "1", "t", "c" * 5001),
    lambda c: writes.add_note(c, "om", "Leads", "1", "t\x00", "c"),
    lambda c: writes.add_note(c, "om", "Leads", "1", 5, "c"),
    lambda c: writes.create_task(c, "om", "", "2026-11-02"),
    lambda c: writes.create_task(c, "om", "S" * 256, "2026-11-02"),
    lambda c: writes.create_task(c, "om", "S\nX", "2026-11-02"),
    lambda c: writes.create_task(c, "om", "S", "02.11.2026"),
    lambda c: writes.create_task(c, "om", "S", "2026-13-40"),
    lambda c: writes.create_task(c, "om", "S", "2026-11-02T10:00:00"),
    lambda c: writes.create_task(c, "om", "S", None),
    lambda c: writes.create_task(c, "om", "S", "2026-11-02", "Leads", None),
    lambda c: writes.create_task(c, "om", "S", "2026-11-02", None, "5"),
    lambda c: writes.create_task(c, "om", "S", "2026-11-02", "Users", "5"),
    lambda c: writes.create_task(c, "om", "S", "2026-11-02", "Leads", "x"),
    lambda c: writes.create_task(c, "om", "S", "2026-11-02", description="d" * 5001),
    lambda c: writes.create_task(c, "evil", "S", "2026-11-02"),
])
async def test_bad_input_rejected_with_zero_network(call, client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await call(client)
    assert token.calls == 0 and not data_calls(router)


async def test_org_without_credentials_rejected_before_network(router):
    only_om = ZohoClient(ZohoConfig.from_env({k: v for k, v in ENV_BOTH.items()
                                              if "ADO" not in k}))
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await writes.create(only_om, "ado", "Leads", {"Last_Name": "x"})
    assert token.calls == 0 and not data_calls(router)


# ---- HTTP behaviour --------------------------------------------------------------------------

@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("down"),
                                   httpx.RemoteProtocolError("cut")])
async def test_write_is_never_retried_after_a_network_error(error, client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(side_effect=error)
    with pytest.raises(ZohoAPIError, match="outcome_unknown"):
        await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert route.call_count == 1


async def test_write_overall_deadline_is_not_retried(client, router, monkeypatch):
    import asyncio

    import integrations.zoho.client as zc
    monkeypatch.setattr(zc, "OVERALL_TIMEOUT_SECONDS", 0.05)
    TokenMock(router)

    seen = []

    async def slow(request):
        seen.append(request)
        await asyncio.sleep(1)
        return ok()

    router.put(f"{API}/crm/v6/Leads/1").mock(side_effect=slow)
    with pytest.raises(ZohoAPIError, match="outcome_unknown"):
        await writes.update(client, "om", "Leads", "1", {"Last_Name": "x"})
    assert len(seen) == 1


async def test_invalid_token_401_is_retried_once_with_a_fresh_token(client, router):
    token = TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(side_effect=[
        httpx.Response(401, json={"code": "INVALID_TOKEN"}), ok()])
    result = await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert result["ok"] is True
    assert route.call_count == 2 and token.calls == 2
    assert route.calls[0].request.headers["authorization"] != \
        route.calls[1].request.headers["authorization"]


async def test_second_401_stops_after_one_retry(client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(
        return_value=httpx.Response(401, json={"code": "INVALID_TOKEN"}))
    with pytest.raises(ZohoAPIError, match="HTTP 401"):
        await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert route.call_count == 2


async def test_other_errors_are_not_retried(client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(return_value=httpx.Response(500, json={}))
    with pytest.raises(ZohoAPIError, match="HTTP 500"):
        await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert route.call_count == 1


async def test_write_never_follows_redirects(client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(return_value=httpx.Response(
        307, headers={"location": "https://evil.example/x"}))
    other = router.post("https://evil.example/x").mock(return_value=httpx.Response(200))
    with pytest.raises(ZohoAPIError):
        await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert route.call_count == 1 and other.call_count == 0


async def test_authorization_header_and_json_content_type(client, router):
    TokenMock(router)
    route = router.post(f"{API}/crm/v6/Leads").mock(return_value=ok())
    await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    req = route.calls.last.request
    assert req.headers["authorization"].startswith("Zoho-oauthtoken AT-om-")
    assert req.headers["content-type"] == "application/json"


async def test_errors_never_contain_secrets(client, router):
    TokenMock(router)
    router.post(f"{API}/crm/v6/Leads").mock(return_value=httpx.Response(
        400, json={"code": SECRETS[0], "message": SECRETS[1]}))
    with pytest.raises(ZohoAPIError) as exc:
        await writes.create(client, "om", "Leads", {"Last_Name": "x"})
    assert not any(secret in str(exc.value) for secret in SECRETS)
