import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.call_e_service import CallEError, CalleService
from app.models import RecoveryRequest, Supplier


def request():
    supplier = Supplier(name="Supplier A", phone="+919876543210")
    return RecoveryRequest(machine="CNC", part_number="P1", part_description="bearing", quantity=20, max_hours=3, downtime_cost_per_hour=5000, suppliers=[supplier], live_confirmed=True, idempotency_key="run-1")


def response(status, payload):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "https://api.heycall-e.com"))


@pytest.mark.parametrize("phone,valid", [("+919876543210", True), ("9876543210", False), ("+91 9876543210", False), ("09123456789", False)])
def test_e164_validation(phone, valid):
    if valid:
        assert CalleService.validate_phone(phone) == phone
    else:
        with pytest.raises(ValueError):
            CalleService.validate_phone(phone)


def test_demo_mode_does_not_require_api_key():
    with patch.dict(os.environ, {"CALL_E_MODE": "demo", "CALLE_API_KEY": ""}, clear=False):
        service = CalleService()
    assert service.call_supplier(Supplier(name="Supplier C", phone="+3"), request())["quantity_available"] == 8


def test_live_create_and_poll_maps_structured_result():
    service = CalleService()
    service.mode = "live"
    service.api_key = "test-key"
    created = response(200, {"id": "call_test", "status": "queued"})
    queued = response(200, {"id": "call_test", "status": "in_progress"})
    completed = response(200, {"id": "call_test", "status": "completed", "recipients": [{"structured_result": {"supplier_name": "Supplier A", "available_quantity": 20, "unit_price": 420, "currency": "INR", "delivery_hours": 6, "can_fulfill": "yes"}}]})

    client = AsyncMock()
    client.post.side_effect = [created]
    client.get.side_effect = [queued, completed]
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False

    with patch("app.call_e_service.httpx.AsyncClient", return_value=client), patch("app.call_e_service.asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(service.call_supplier_async(request().suppliers[0], request()))
    assert result["call_id"] == "call_test"
    assert result["quantity_available"] == 20
    assert result["source"] == "CALL-E LIVE CALL"
    assert client.post.call_args.kwargs["headers"]["Idempotency-Key"] == "restartai-run-1-Supplier A"


def test_independent_test_calls_use_different_idempotency_keys():
    service = CalleService()
    service.api_key = "test-key"
    client = AsyncMock()
    client.post.side_effect = [response(200, {"id": "call_one", "status": "queued"}), response(200, {"id": "call_two", "status": "queued"})]
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    supplier_phone = "+919876543210"
    with patch("app.call_e_service.httpx.AsyncClient", return_value=client):
        asyncio.run(service.start_test_call(supplier_phone, "Supplier A"))
        asyncio.run(service.start_test_call(supplier_phone, "Supplier A"))
    keys = [call.kwargs["headers"]["Idempotency-Key"] for call in client.post.call_args_list]
    assert keys[0] != keys[1]
    assert all(supplier_phone not in key for key in keys)
    assert all(key.startswith("restartai-test-") for key in keys)


def test_same_logical_recovery_retry_reuses_idempotency_key():
    service = CalleService()
    service.mode = "live"
    service.api_key = "test-key"
    service.max_retries = 1
    failed = {"id": "call_retry_one", "status": "failed", "failure_code": "call_failed", "failure_message": "NO ANSWER", "recipients": [{"attempts": [{"failure_code": "480", "failure_message": "Not available"}]}]}
    completed = {"id": "call_retry_two", "status": "completed", "recipients": [{"structured_result": {"supplier_name": "Supplier A", "available_quantity": 20, "unit_price": 10, "currency": "INR", "delivery_hours": 2, "can_fulfill": "yes"}}]}
    client = AsyncMock()
    client.post.side_effect = [response(200, {"id": "call_retry_one", "status": "queued"}), response(200, {"id": "call_retry_two", "status": "queued"})]
    client.get.side_effect = [response(200, failed), response(200, completed)]
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    with patch("app.call_e_service.httpx.AsyncClient", return_value=client), patch("app.call_e_service.asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(service.call_supplier_async(request().suppliers[0], request(), idempotency_key="restartai-run-retry-supplier-a"))
    keys = [call.kwargs["headers"]["Idempotency-Key"] for call in client.post.call_args_list]
    assert keys == ["restartai-run-retry-supplier-a", "restartai-run-retry-supplier-a"]
    assert result["confirmed"] is True


def test_different_supplier_logical_calls_use_different_keys():
    service = CalleService()
    service.api_key = "test-key"
    client = AsyncMock()
    client.post.side_effect = [response(200, {"id": "call_a", "status": "queued"}), response(200, {"id": "call_b", "status": "queued"})]
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    suppliers = [Supplier(name="Supplier A", phone="+919876543210"), Supplier(name="Supplier B", phone="+919876543211")]
    with patch("app.call_e_service.httpx.AsyncClient", return_value=client):
        asyncio.run(service.create_call(suppliers[0], request()))
        asyncio.run(service.create_call(suppliers[1], request()))
    keys = [call.kwargs["headers"]["Idempotency-Key"] for call in client.post.call_args_list]
    assert keys[0] != keys[1]
    assert keys == ["restartai-run-1-Supplier A", "restartai-run-1-Supplier B"]


def test_idempotency_conflict_has_clear_user_message():
    service = CalleService()
    error = service._error_from_response(response(409, {"code": "idempotency_conflict", "message": "key reused"}), "create")
    assert error.code == "idempotency_conflict"
    assert "different request" in str(error)


def test_supplier_task_contains_evidence_based_questions():
    service = CalleService()
    task = service._build_task(request().suppliers[0], request(), [])
    assert "Confirm the supplier or business identity" in task
    assert "Do not invent or infer numerical values" in task
    assert "P1" in task


def test_test_call_uses_telephony_task_payload():
    service = CalleService()
    payload = service._build_test_payload(Supplier(name="Test Supplier", phone="+919876543210"))
    assert "whether they can hear you" in payload["task"]
    assert payload["recipient_result_schema"]["required"] == ["answered"]


@pytest.mark.parametrize("status,detail", [(401, "authentication"), (422, "rejected"), (429, "rate limit")])
def test_live_http_errors_are_explicit(status, detail):
    service = CalleService()
    service.api_key = "test-key"
    client = AsyncMock()
    client.post.return_value = response(status, {"message": detail})
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    with patch("app.call_e_service.httpx.AsyncClient", return_value=client):
        with pytest.raises(CallEError, match=detail if status not in {401, 422, 429} else "CALL-E"):
            asyncio.run(service.create_call(request().suppliers[0], request()))


def test_live_mode_never_uses_demo():
    service = CalleService()
    service.mode = "live"
    with pytest.raises(CallEError):
        service.call_supplier(Supplier(name="Supplier C", phone="+919876543210"), request())


def test_failed_status_preserves_nested_diagnostics_without_secrets():
    service = CalleService()
    service.api_key = "test-key"
    service.calls["call_failed"] = {"call_id": "call_failed", "phone": "+919876543210"}
    failed = response(200, {
        "id": "call_failed",
        "status": "failed",
        "failure_code": "call_failed",
        "failure_message": "calling task status=FAILED",
        "recipients": [{"status": "failed", "phone": "+919876543210", "attempts": [{"status": "no_answer", "failure_code": "no_answer", "failure_message": "Hangup by: bot"}]}],
        "transcript": "private transcript",
        "api_key": "must-not-leak",
    })
    client = AsyncMock()
    client.get.return_value = failed
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    with patch("app.call_e_service.httpx.AsyncClient", return_value=client):
        result = asyncio.run(service.test_call_status("call_failed"))
    assert result["success"] is True
    assert result["status"] == "failed"
    assert result["diagnostics"]["recipient_status"] == "failed"
    assert result["diagnostics"]["attempt_failure_message"] == "Hangup by: bot"
    assert result["diagnostics"]["transcript_available"] is True
    assert "api_key" not in result["provider_response"]
    assert result["provider_response"]["transcript"] == "[redacted; transcript available]"
    assert result["provider_response"]["recipients"][0]["phone"] == "+91******3210"


def test_no_answer_480_is_classified_without_overwriting_original_values():
    service = CalleService()
    data = {"id": "call_480", "status": "failed", "failure_code": "call_failed", "failure_message": "calling task status=NO ANSWER (Hangup by: bot)", "recipients": [{"status": "failed", "attempts": [{"status": "failed", "failure_code": "480", "failure_message": "Not available"}]}]}
    diagnostics = service._diagnostics("call_480", data)
    assert diagnostics["category"] == "supplier_unavailable"
    assert diagnostics["human_message"] == "Supplier did not answer or was unavailable."
    assert diagnostics["failure_code"] == "call_failed"
    assert diagnostics["failure_message"].startswith("calling task status=NO ANSWER")
    assert diagnostics["attempt_failure_code"] == "480"


def test_other_failed_code_is_not_classified_as_supplier_unavailable():
    service = CalleService()
    diagnostics = service._diagnostics("call_500", {"id": "call_500", "status": "failed", "failure_code": "call_failed", "failure_message": "provider error", "recipients": [{"attempts": [{"failure_code": "500", "failure_message": "Internal provider failure"}]}]})
    assert diagnostics["category"] == "provider_failure"
    assert diagnostics["retryable"] is False


def test_events_are_retrieved_from_call_e():
    service = CalleService()
    service.api_key = "test-key"
    service.calls["call_events"] = {"call_id": "call_events"}
    events = response(200, {"events": [{"type": "dialing"}, {"type": "failed"}]})
    client = AsyncMock()
    client.get.return_value = events
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    with patch("app.call_e_service.httpx.AsyncClient", return_value=client):
        result = asyncio.run(service.get_call_events("call_events"))
    assert result == [{"type": "dialing"}, {"type": "failed"}]
    assert "/v1/calls/call_events/events" in client.get.call_args.args[0]


def test_completed_incomplete_result_preserves_nulls_and_is_not_confirmed():
    service = CalleService()
    service.mode = "live"
    service.api_key = "test-key"
    created = response(200, {"id": "call_incomplete", "status": "queued"})
    completed = response(200, {"id": "call_incomplete", "status": "completed", "recipients": [{"structured_result": {"supplier_name": "Supplier A", "available_quantity": 20, "can_fulfill": "unknown"}}]})
    client = AsyncMock()
    client.post.return_value = created
    client.get.return_value = completed
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    with patch("app.call_e_service.httpx.AsyncClient", return_value=client):
        result = asyncio.run(service.call_supplier_async(request().suppliers[0], request()))
    assert result["status"] == "INCOMPLETE OFFER"
    assert result["quantity_available"] == 20
    assert result["unit_price"] is None
    assert result["availability_hours"] is None
    assert result["confirmed"] is False


def test_unknown_fulfillment_is_not_confirmed():
    service = CalleService()
    data = {"id": "call_unknown", "status": "failed", "failure_code": "call_failed", "failure_message": "unknown"}
    diagnostics = service._diagnostics("call_unknown", data)
    assert diagnostics["status"] == "failed"
