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
    assert "category" not in diagnostics
