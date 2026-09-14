import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from .models import RecoveryRequest, Supplier

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
logger = logging.getLogger(__name__)


class CallEError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None, call_id: str | None = None, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.call_id = call_id
        self.diagnostics = diagnostics or {}


class CallETimeoutError(CallEError):
    pass


class CalleService:
    def __init__(self):
        self.mode = os.getenv("CALL_E_MODE", "demo").lower()
        self.api_key = os.getenv("CALLE_API_KEY", "")
        self.base_url = os.getenv("CALLE_BASE_URL", "https://api.heycall-e.com").rstrip("/")
        self.calls: dict[str, dict[str, Any]] = {}

    @staticmethod
    def validate_phone(phone: str) -> str:
        normalized = phone.strip()
        if not E164_RE.fullmatch(normalized):
            raise ValueError("Phone must be a valid E.164 number, for example +919876543210.")
        return normalized

    @staticmethod
    def mask_phone(phone: str) -> str:
        return f"{phone[:3]}******{phone[-4:]}" if len(phone) > 7 else "***"

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        if not self.api_key:
            raise CallEError("CALL-E API key is missing. Add CALLE_API_KEY to .env.", code="missing_api_key")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _result_schema() -> dict[str, Any]:
        fields = {
            "supplier_name": {"type": "string", "description": "Supplier name or business name given during the call."},
            "available_quantity": {"type": "integer", "description": "Number of units the supplier can provide."},
            "unit_price": {"type": "number", "description": "Price per unit offered by the supplier."},
            "currency": {"type": "string", "description": "Currency of the quoted unit price, such as INR."},
            "delivery_hours": {"type": "number", "description": "Number of hours until delivery."},
            "can_fulfill": {"type": "string", "enum": ["yes", "partial", "no", "unknown"]},
        }
        return {"type": "object", "required": list(fields), "properties": fields, "additionalProperties": False}

    def _build_payload(self, supplier: Supplier, request: RecoveryRequest, known_offers: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "task": self._build_task(supplier, request, known_offers),
            "recipients": [{"phones": [supplier.phone], "region": supplier.region, "locale": supplier.locale}],
            "result_schema": {"type": "object", "required": ["supplier_call_completed"], "properties": {"supplier_call_completed": {"type": "boolean"}}, "additionalProperties": False},
            "recipient_result_schema": self._result_schema(),
            "metadata": {"app": "restartai", "workflow_run_id": request.idempotency_key or "", "supplier_name": supplier.name, "part_number": request.part_number},
        }

    @staticmethod
    def _error_from_response(response: httpx.Response, action: str) -> CallEError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("message") or body.get("error") or body.get("detail") or response.text[:300]
        messages = {401: "CALL-E authentication failed. Check CALLE_API_KEY.", 403: "CALL-E authorization was denied.", 422: "CALL-E rejected the call request. Check recipient phone format and request schema.", 429: "CALL-E rate limit reached. Try again later."}
        return CallEError(messages.get(response.status_code, f"CALL-E {action} failed ({response.status_code}): {detail}"), code=str(response.status_code), status_code=response.status_code)

    async def create_call(self, supplier: Supplier, request: RecoveryRequest, known_offers=None, *, idempotency_key: str | None = None) -> dict[str, Any]:
        phone = self.validate_phone(supplier.phone)
        key = idempotency_key or f"restartai-{request.idempotency_key or request.part_number}-{supplier.name}"
        logger.info("CALL-E create request supplier=%s phone=%s", supplier.name, self.mask_phone(phone))
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{self.base_url}/v1/calls", headers=self._headers(key), json=self._build_payload(supplier, request, known_offers or []))
        except httpx.TimeoutException as exc:
            raise CallEError("CALL-E request timed out.", code="network_timeout") from exc
        except httpx.HTTPError as exc:
            raise CallEError(f"CALL-E connection failed: {exc}", code="connection_error") from exc
        if response.is_error:
            raise self._error_from_response(response, "create")
        try:
            data = response.json()
        except ValueError as exc:
            raise CallEError("CALL-E returned a non-JSON create response.", code="invalid_response", status_code=response.status_code) from exc
        call_id = data.get("id") or data.get("call_id")
        if not call_id:
            raise CallEError("CALL-E returned no call ID.", code="invalid_response")
        self.calls[call_id] = {"supplier": supplier.name, "phone": phone, "phone_masked": self.mask_phone(phone), "call_id": call_id, "status": data.get("status", "queued"), "created_at": datetime.now(timezone.utc).isoformat(), "failure_code": None, "failure_message": None, "result": None, "summary": None, "confidence": None}
        logger.info("CALL-E response call_id=%s status=%s", call_id, data.get("status", "queued"))
        return data

    async def get_call(self, call_id: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{self.base_url}/v1/calls/{call_id}", headers=self._headers())
        except httpx.TimeoutException as exc:
            raise CallEError("CALL-E status request timed out.", code="network_timeout") from exc
        except httpx.HTTPError as exc:
            raise CallEError(f"CALL-E connection failed: {exc}", code="connection_error") from exc
        if response.is_error:
            raise self._error_from_response(response, "status")
        try:
            return response.json()
        except ValueError as exc:
            raise CallEError("CALL-E returned a non-JSON status response.", code="invalid_response", status_code=response.status_code) from exc

    @staticmethod
    def _status(data: dict[str, Any]) -> str:
        recipients = data.get("recipients") or [{}]
        return str(data.get("status") or data.get("state") or recipients[0].get("status") or "unknown").lower()

    @staticmethod
    def _first_nested(data: Any, keys: tuple[str, ...]) -> Any:
        if isinstance(data, dict):
            for key in keys:
                if data.get(key) is not None:
                    return data[key]
            for value in data.values():
                found = CalleService._first_nested(value, keys)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for value in data:
                found = CalleService._first_nested(value, keys)
                if found is not None:
                    return found
        return None

    @classmethod
    def _diagnostics(cls, call_id: str, data: dict[str, Any]) -> dict[str, Any]:
        recipients = data.get("recipients") if isinstance(data.get("recipients"), list) else []
        recipient = recipients[0] if recipients and isinstance(recipients[0], dict) else {}
        attempts = recipient.get("attempts") if isinstance(recipient.get("attempts"), list) else data.get("attempts")
        attempt = attempts[0] if isinstance(attempts, list) and attempts and isinstance(attempts[0], dict) else {}
        diagnostics = {
            "call_id": call_id,
            "status": cls._status(data),
            "failure_code": data.get("failure_code") or data.get("error_code"),
            "failure_message": data.get("failure_message") or data.get("error") or data.get("failure"),
            "recipient_status": recipient.get("status") or cls._first_nested(data, ("recipient_status",)),
            "attempt_status": attempt.get("status") or cls._first_nested(data, ("attempt_status",)),
            "attempt_failure_code": attempt.get("failure_code") or attempt.get("error_code"),
            "attempt_failure_message": attempt.get("failure_message") or attempt.get("error") or attempt.get("failure"),
            "structured_result": cls._extract_result(data),
            "transcript_available": bool(cls._first_nested(data, ("transcript",))),
        }
        cleaned = {key: value for key, value in diagnostics.items() if value not in (None, "", {})}
        attempt_code = str(cleaned.get("attempt_failure_code", "")).lower()
        attempt_message = str(cleaned.get("attempt_failure_message", "")).lower()
        failure_message = str(cleaned.get("failure_message", "")).lower()
        no_answer_evidence = (
            attempt_code == "480"
            or "no answer" in attempt_message
            or "no answer" in failure_message
            or "unavailable" in attempt_message
            or "unavailable" in failure_message
        )
        if no_answer_evidence and cleaned.get("status") == "failed":
            cleaned["category"] = "supplier_unavailable"
            cleaned["human_message"] = "Supplier did not answer or was unavailable."
        return cleaned

    @classmethod
    def _safe_response(cls, value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {name: cls._safe_response(item, name) for name, item in value.items() if name.lower() not in {"api_key", "authorization", "access_token", "secret"}}
        if isinstance(value, list):
            return [cls._safe_response(item, key) for item in value]
        if key.lower() in {"phone", "phones", "phone_number", "phone_numbers"}:
            if isinstance(value, str):
                return cls.mask_phone(value)
            if isinstance(value, list):
                return [cls.mask_phone(str(item)) for item in value]
        if key.lower() == "transcript":
            return "[redacted; transcript available]"
        return value

    def _log_result(self, call_id: str, data: dict[str, Any]) -> None:
        diagnostics = self._diagnostics(call_id, data)
        logger.info("LIVE CALL-E RESULT call_id=%s status=%s diagnostics=%s", call_id, diagnostics.get("status"), diagnostics)

    def _update_record(self, call_id: str, data: dict[str, Any]) -> dict[str, Any]:
        record = self.calls.setdefault(call_id, {"call_id": call_id})
        status = self._status(data)
        record.update({"status": status, "failure_code": data.get("failure_code") or data.get("error_code"), "failure_message": data.get("failure_message") or data.get("error") or data.get("failure")})
        if status in {"completed", "failed", "canceled"}:
            record["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("CALL-E status call_id=%s status=%s", call_id, status)
        record["provider_response"] = data
        record["diagnostics"] = self._diagnostics(call_id, data)
        record["category"] = record["diagnostics"].get("category")
        record["human_message"] = record["diagnostics"].get("human_message")
        if status in {"failed", "canceled"}:
            self._log_result(call_id, data)
        return record

    async def wait_for_call(self, call_id: str, *, max_wait_seconds: int = 300, poll_interval: float = 2.0) -> dict[str, Any]:
        attempts = max(1, int(max_wait_seconds / poll_interval))
        for attempt in range(attempts):
            data = await self.get_call(call_id)
            record = self._update_record(call_id, data)
            if record["status"] in {"completed", "failed", "canceled"}:
                return data
            if attempt < attempts - 1:
                await asyncio.sleep(poll_interval)
        raise CallETimeoutError("CALL-E call did not reach a terminal state within 5 minutes.", code="timeout")

    @staticmethod
    def _extract_result(data: dict[str, Any]) -> dict[str, Any]:
        recipients = data.get("recipients") or []
        recipient = recipients[0] if recipients else {}
        # Recipient-level structured_result holds the detailed recipient_result_schema
        # data (quantity, price, delivery, etc.). The top-level structured_result only
        # reflects the batch-level result_schema (e.g. supplier_call_completed), so it
        # must be checked last, not first.
        return recipient.get("structured_result") or recipient.get("result") or data.get("structured_result") or data.get("result") or {}

    async def call_supplier_async(self, supplier: Supplier, request: RecoveryRequest, known_offers=None, *, idempotency_key: str | None = None) -> dict[str, Any]:
        created = await self.create_call(supplier, request, known_offers, idempotency_key=idempotency_key)
        call_id = created.get("id") or created.get("call_id")
        completed = await self.wait_for_call(call_id)
        status = self._status(completed)
        record = self._update_record(call_id, completed)
        if status != "completed":
            diagnostics = record.get("diagnostics", {})
            raise CallEError(record.get("failure_message") or f"CALL-E call {status}.", code=record.get("failure_code") or status, call_id=call_id, diagnostics=diagnostics)
        result = self._extract_result(completed)
        if not result:
            raise CallEError("CALL-E completed without a usable structured supplier result.", code="missing_result")
        normalized = {"supplier": result.get("supplier_name") or supplier.name, "phone": supplier.phone, "quantity_available": result.get("available_quantity", 0), "unit_price": result.get("unit_price", 0), "currency": result.get("currency", "INR"), "availability_hours": result.get("delivery_hours", 999), "compatible": result.get("can_fulfill") in {"yes", "partial"}, "confirmed": result.get("can_fulfill") in {"yes", "partial"}, "compatibility_confidence": result.get("confidence", 1), "delivery_method": "delivery", "status": "FULL STOCK" if result.get("can_fulfill") == "yes" else "PARTIAL STOCK" if result.get("can_fulfill") == "partial" else "NO STOCK", "source": "CALL-E LIVE CALL", "call_id": call_id, "notes": result.get("summary", "")}
        record.update({"result": result, "summary": result.get("summary"), "confidence": result.get("confidence")})
        return normalized

    async def start_test_call(self, phone: str, supplier_name: str) -> dict[str, Any]:
        phone = self.validate_phone(phone)
        supplier = Supplier(name=supplier_name, phone=phone)
        request = RecoveryRequest(machine="test", part_number="test", part_description="test", quantity=1, max_hours=1, downtime_cost_per_hour=0, suppliers=[supplier], live_confirmed=True)
        created = await self.create_call(supplier, request, idempotency_key=f"restartai-test-{phone}-{uuid.uuid4()}")
        call_id = created.get("id") or created.get("call_id")
        record = self.calls[call_id]
        return {"call_id": call_id, "status": record["status"], "supplier": supplier_name, "phone_masked": record["phone_masked"]}

    async def test_call_status(self, call_id: str) -> dict[str, Any]:
        data = await self.get_call(call_id)
        record = self._update_record(call_id, data)
        return {
            "success": True,
            "call_id": call_id,
            "status": record["status"],
            "failure_code": record.get("failure_code"),
            "failure_message": record.get("failure_message"),
            "category": record.get("category"),
            "human_message": record.get("human_message"),
            "diagnostics": record.get("diagnostics", {}),
            "structured_result": record.get("diagnostics", {}).get("structured_result"),
            "transcript_available": record.get("diagnostics", {}).get("transcript_available", False),
            "provider_response": self._safe_response(data),
        }

    async def get_call_status(self, call_id: str) -> dict[str, Any]:
        return await self.test_call_status(call_id)

    def call_supplier(self, supplier: Supplier, request: RecoveryRequest, known_offers=None) -> dict[str, Any]:
        if self.mode == "demo":
            return self._demo_call(supplier, request, known_offers or [])
        raise CallEError("Live calls must use the asynchronous CALL-E REST flow.", code="async_required")

    def _build_task(self, supplier, request, known_offers):
        prior = ""
        if known_offers:
            prior = "\nInformation already discovered from other suppliers:\n" + "\n".join(f"- {x['supplier']}: {x.get('quantity_available', 0)} units, ₹{x.get('unit_price', 0)}/unit, {x.get('availability_hours', 999)}h" for x in known_offers)
        remaining = max(0, request.quantity - sum(x.get("quantity_available", 0) for x in known_offers if x.get("compatible") and x.get("confirmed")))
        return f"""You are RestartAI, an emergency manufacturing recovery agent.

Call {supplier.name} and determine whether they can urgently supply the required replacement component.
Component: {request.part_number} — {request.part_description}
Required quantity: {request.quantity}
Production line: {request.machine}
Production downtime cost: ₹{request.downtime_cost_per_hour} per hour
Remaining quantity after prior committed offers: {remaining}

Ask the supplier for available quantity, unit price and currency, delivery time, and whether they can fulfill the full or partial quantity. Do not invent information. If a value is not provided, return unknown/0 according to the structured result schema.
{prior}""".strip()

    def _demo_call(self, supplier, request, known_offers):
        demo = {"Supplier A": dict(quantity_available=20, unit_price=150, availability_hours=24, delivery_method="delivery", compatible=True, compatibility_confidence=.98, confirmed=True, notes="Full stock, arrives tomorrow."), "Supplier B": dict(quantity_available=20, unit_price=220, availability_hours=2, delivery_method="delivery", compatible=True, compatibility_confidence=.98, confirmed=True, notes="Full stock, 2-hour delivery."), "Supplier C": dict(quantity_available=8, unit_price=180, availability_hours=.5, delivery_method="pickup", compatible=True, compatibility_confidence=.95, confirmed=True, notes="Only 8 available for immediate pickup."), "Supplier D": dict(quantity_available=12, unit_price=190, availability_hours=1.5, delivery_method="delivery", compatible=True, compatibility_confidence=.96, confirmed=True, notes="12 available; 90-minute delivery.")}
        raw = demo.get(supplier.name, {"quantity_available": 0, "unit_price": 0, "availability_hours": 999, "delivery_method": "unknown", "compatible": False, "compatibility_confidence": 0, "confirmed": False, "notes": "No demo data for this supplier."})
        status = "FULL STOCK" if raw["quantity_available"] >= request.quantity else ("PARTIAL STOCK" if raw["quantity_available"] > 0 else "NO STOCK")
        return {"supplier": supplier.name, "phone": supplier.phone, **raw, "status": status, "currency": "INR", "source": "demo", "call_id": f"demo-{supplier.name.lower().replace(' ', '-') }"}