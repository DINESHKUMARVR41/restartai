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
        self.max_retries = max(0, int(os.getenv("CALL_E_MAX_RETRIES", "1")))

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
            "supplier_name": {"type": "string", "description": "Supplier/business name stated by recipient."},
            "product_match": {"type": "string", "enum": ["exact", "equivalent", "mismatch", "unknown"], "description": "Exact part, confirmed equivalent, mismatch, or insufficient evidence."},
            "available_quantity": {"type": "integer", "description": "Units explicitly confirmed available."},
            "unit_price": {"type": "number", "description": "Explicit quoted unit price."},
            "currency": {"type": "string", "enum": ["INR", "USD", "EUR", "GBP", "OTHER", "UNKNOWN"], "description": "Explicit currency."},
            "total_price": {"type": "number", "description": "Explicit total quoted price if stated."},
            "delivery_hours": {"type": "number", "description": "Earliest delivery time in hours."},
            "delivery_method": {"type": "string", "description": "Delivery, pickup, courier, or explicitly stated method."},
            "shipping_cost": {"type": "number", "description": "Explicit shipping/delivery charge if stated."},
            "tax_included": {"type": "string", "enum": ["yes", "no", "unknown"], "description": "Whether quoted price explicitly includes tax."},
            "can_fulfill": {"type": "string", "enum": ["yes", "partial", "no", "unknown"], "description": "Full, partial, no, or unknown."},
            "stock_location": {"type": "string", "description": "Stock location only if explicitly stated."},
            "payment_terms": {"type": "string", "description": "Payment terms only if explicitly stated."},
            "quote_validity_hours": {"type": "number", "description": "Quote validity only if explicitly stated."},
            "additional_charges": {"type": "string", "description": "Explicit extra fees/minimum-order conditions."},
            "summary": {"type": "string", "description": "Short factual summary."},
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
    def _build_test_payload(supplier: Supplier) -> dict[str, Any]:
        return {
            "task": "You are testing RestartAI's supplier phone integration. Call the recipient, introduce yourself as an automated supplier verification agent, ask whether they can hear you, wait for their response, and politely end the call. Return whether the recipient answered.",
            "recipients": [{"phones": [supplier.phone], "region": supplier.region, "locale": supplier.locale}],
            "result_schema": {"type": "object", "required": ["answered"], "properties": {"answered": {"type": "string", "enum": ["yes", "no", "unknown"], "description": "Use yes only if the recipient clearly answers and responds; no if terminal without a response; unknown if evidence is insufficient."}}, "additionalProperties": False},
            "recipient_result_schema": {"type": "object", "required": ["answered"], "properties": {"answered": {"type": "string", "enum": ["yes", "no", "unknown"]}}, "additionalProperties": False},
            "metadata": {"app": "restartai", "purpose": "telephony_test", "supplier_name": supplier.name},
        }

    @staticmethod
    def _error_from_response(response: httpx.Response, action: str) -> CallEError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        provider_code = body.get("code") if isinstance(body, dict) else None
        detail = body.get("message") or body.get("error") or body.get("detail") or response.text[:300]
        if response.status_code == 409 and provider_code == "idempotency_conflict":
            return CallEError("CALL-E rejected this request because the idempotency key was already used for a different request. A new idempotency key will be generated for the next independent call.", code=provider_code, status_code=409)
        messages = {401: "CALL-E authentication failed. Check CALLE_API_KEY.", 403: "CALL-E authorization was denied.", 422: "CALL-E rejected the call request. Check recipient phone format and request schema.", 429: "CALL-E rate limit reached. Try again later."}
        return CallEError(messages.get(response.status_code, f"CALL-E {action} failed ({response.status_code}): {detail}"), code=str(response.status_code), status_code=response.status_code)

    async def create_call(self, supplier: Supplier, request: RecoveryRequest, known_offers=None, *, idempotency_key: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        phone = self.validate_phone(supplier.phone)
        key = idempotency_key or f"restartai-{request.idempotency_key or request.part_number}-{supplier.name}"
        logger.info("CALL-E create request supplier=%s phone=%s", supplier.name, self.mask_phone(phone))
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{self.base_url}/v1/calls", headers=self._headers(key), json=payload or self._build_payload(supplier, request, known_offers or []))
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
        if "failure_code" in cleaned:
            cleaned["raw_failure_code"] = cleaned["failure_code"]
        if "failure_message" in cleaned:
            cleaned["raw_failure_message"] = cleaned["failure_message"]
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
            cleaned["retryable"] = True
        elif cleaned.get("status") == "canceled":
            cleaned["category"] = "canceled"
            cleaned["human_message"] = "CALL-E canceled the call."
            cleaned["retryable"] = False
        elif cleaned.get("status") == "failed":
            raw_code = str(cleaned.get("attempt_failure_code") or cleaned.get("failure_code") or "").lower()
            category = "provider_failure" if raw_code.startswith("5") or "provider" in failure_message else "telephony_failure"
            cleaned["category"] = category
            cleaned["human_message"] = "The telephony provider could not complete the call."
            cleaned["retryable"] = category == "telephony_failure"
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

    async def get_call_events(self, call_id: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{self.base_url}/v1/calls/{call_id}/events", headers=self._headers())
        except httpx.TimeoutException as exc:
            raise CallEError("CALL-E events request timed out.", code="network_timeout") from exc
        except httpx.HTTPError as exc:
            raise CallEError(f"CALL-E connection failed: {exc}", code="connection_error") from exc
        if response.is_error:
            raise self._error_from_response(response, "events")
        try:
            data = response.json()
        except ValueError as exc:
            raise CallEError("CALL-E returned a non-JSON events response.", code="invalid_response", status_code=response.status_code) from exc
        events = data if isinstance(data, list) else data.get("events", []) if isinstance(data, dict) else []
        return self._safe_response(events)

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
        return data.get("structured_result") or data.get("result") or recipient.get("structured_result") or recipient.get("result") or {}

    async def call_supplier_async(self, supplier: Supplier, request: RecoveryRequest, known_offers=None, *, idempotency_key: str | None = None) -> dict[str, Any]:
        created = await self.create_call(supplier, request, known_offers, idempotency_key=idempotency_key)
        call_id = created.get("id") or created.get("call_id")
        completed = await self.wait_for_call(call_id)
        status = self._status(completed)
        record = self._update_record(call_id, completed)
        retry_number = 0
        while status == "failed" and record.get("diagnostics", {}).get("retryable") and retry_number < self.max_retries:
            retry_number += 1
            logger.info("CALL-E bounded retry supplier=%s retry=%s", supplier.name, retry_number)
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
            raise CallEError("CALL-E completed without a usable structured supplier result.", code="missing_result", call_id=call_id)
        quantity = result.get("available_quantity")
        unit_price = result.get("unit_price")
        delivery_hours = result.get("delivery_hours")
        currency = result.get("currency") or "UNKNOWN"
        fulfillment = result.get("can_fulfill", "unknown")
        product_match = result.get("product_match", "unknown")
        complete_offer = (
            quantity is not None and unit_price is not None and delivery_hours is not None
            and currency != "UNKNOWN" and fulfillment in {"yes", "partial"}
            and product_match in {"exact", "equivalent", "unknown"}
        )
        normalized = {
            "supplier": result.get("supplier_name") or supplier.name, "phone": supplier.phone,
            "quantity_available": quantity, "unit_price": unit_price, "currency": currency,
            "availability_hours": delivery_hours,
            "compatible": complete_offer, "confirmed": complete_offer,
            "compatibility_confidence": result.get("confidence", 1) if complete_offer else 0,
            "delivery_method": result.get("delivery_method") or "unknown",
            "status": "FULL STOCK" if complete_offer and fulfillment == "yes" else "PARTIAL STOCK" if complete_offer else "INCOMPLETE OFFER",
            "source": "CALL-E LIVE CALL", "call_id": call_id, "notes": result.get("summary", ""),
            "product_match": product_match, "shipping_cost": result.get("shipping_cost"),
            "tax_included": result.get("tax_included", "unknown"), "total_price": result.get("total_price"),
            "stock_location": result.get("stock_location", ""), "payment_terms": result.get("payment_terms", ""),
            "quote_validity_hours": result.get("quote_validity_hours"),
            "additional_charges": result.get("additional_charges", ""),
        }
        record.update({"result": result, "summary": result.get("summary"), "confidence": result.get("confidence")})
        return normalized

    async def start_test_call(self, phone: str, supplier_name: str) -> dict[str, Any]:
        phone = self.validate_phone(phone)
        supplier = Supplier(name=supplier_name, phone=phone)
        request = RecoveryRequest(machine="telephony test", part_number="telephony test", part_description="telephony test", quantity=1, max_hours=1, downtime_cost_per_hour=0, suppliers=[supplier], live_confirmed=True)
        idempotency_key = f"restartai-test-{uuid.uuid4()}"
        created = await self.create_call(supplier, request, idempotency_key=idempotency_key, payload=self._build_test_payload(supplier))
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
            prior = "\nKnown confirmed offers from other suppliers:\n" + "\n".join(
                f"- {x.get('supplier')}: {x.get('quantity_available', 'unknown')} units, "
                f"{x.get('unit_price', 'unknown')} {x.get('currency', '')}/unit, "
                f"{x.get('availability_hours', 'unknown')}h"
                for x in known_offers
            )
        remaining = max(0, request.quantity - sum(
            x.get("quantity_available", 0) for x in known_offers
            if x.get("compatible") and x.get("confirmed")
        ))
        return f"""You are RestartAI's emergency industrial procurement agent calling {supplier.name}.
The production line is down. Your job is to collect a factual supplier quote for human approval.

INCIDENT
Machine: {request.machine}
Part number: {request.part_number}
Part description: {request.part_description}
Quantity required: {request.quantity}
Remaining quantity needed: {remaining}
Maximum recovery target: {request.max_hours} hours
Compatibility notes: {request.compatibility_notes or "None"}
{prior}

Have a natural phone conversation. Never ask the supplier to speak JSON.
Ask naturally:
1. Confirm the supplier or business identity.
2. Confirm whether the exact part number is in stock, or whether a specific equivalent is available.
3. If equivalent, ask for brand/model and compatibility details.
4. Ask how many units can be supplied immediately.
5. Ask the unit price and currency.
6. Ask the earliest delivery time and whether it is delivery, courier, or pickup.
7. Ask whether GST/tax, shipping, packing and other charges are included; capture explicit extra charges.
8. Ask where the stock is located.
9. Ask about minimum order requirements and payment terms.
10. Ask how long the quote is valid.
11. Confirm full or partial fulfillment.

RULES
- Record only facts explicitly stated by the supplier.
- Do not invent or infer numerical values. Never invent price, quantity, delivery time, tax, fees, location, or terms.
- Missing information must remain unknown/null.
- Do not authorize a purchase.
- Treat an equivalent as different from an exact part match.
- Give a concise factual summary at the end."""

    def _demo_call(self, supplier, request, known_offers):
        demo = {"Supplier A": dict(quantity_available=20, unit_price=150, availability_hours=24, delivery_method="delivery", compatible=True, compatibility_confidence=.98, confirmed=True, notes="Full stock, arrives tomorrow."), "Supplier B": dict(quantity_available=20, unit_price=220, availability_hours=2, delivery_method="delivery", compatible=True, compatibility_confidence=.98, confirmed=True, notes="Full stock, 2-hour delivery."), "Supplier C": dict(quantity_available=8, unit_price=180, availability_hours=.5, delivery_method="pickup", compatible=True, compatibility_confidence=.95, confirmed=True, notes="Only 8 available for immediate pickup."), "Supplier D": dict(quantity_available=12, unit_price=190, availability_hours=1.5, delivery_method="delivery", compatible=True, compatibility_confidence=.96, confirmed=True, notes="12 available; 90-minute delivery.")}
        raw = demo.get(supplier.name, {"quantity_available": 0, "unit_price": 0, "availability_hours": 999, "delivery_method": "unknown", "compatible": False, "compatibility_confidence": 0, "confirmed": False, "notes": "No demo data for this supplier."})
        status = "FULL STOCK" if raw["quantity_available"] >= request.quantity else ("PARTIAL STOCK" if raw["quantity_available"] > 0 else "NO STOCK")
        return {"supplier": supplier.name, "phone": supplier.phone, **raw, "status": status, "currency": "INR", "source": "demo", "call_id": f"demo-{supplier.name.lower().replace(' ', '-') }"}