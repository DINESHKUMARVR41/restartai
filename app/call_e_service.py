import os
import re
from typing import Any
from .models import Supplier, RecoveryRequest

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class CalleService:
    def __init__(self):
        self.mode = os.getenv("CALL_E_MODE", "demo").lower()
        self.api_key = os.getenv("CALLE_API_KEY", "")
        self.base_url = os.getenv("CALLE_BASE_URL", "https://api.heycall-e.com")

    def call_supplier(self, supplier: Supplier, request: RecoveryRequest, known_offers=None) -> dict[str, Any]:
        if self.mode != "live":
            return self._demo_call(supplier, request, known_offers or [])

        if not self.api_key:
            raise RuntimeError("CALL-E API key is missing. Add CALLE_API_KEY to .env; do not put it in the browser.")
        if not E164_RE.fullmatch(supplier.phone):
            raise ValueError(f"{supplier.name}: phone must be a valid E.164 number (example: +14155550100).")

        from calle import CalleClient

        task = self._build_task(supplier, request, known_offers or [])
        schema = {
            "type": "object",
            "required": ["quantity_available", "unit_price", "availability_hours",
                         "delivery_method", "compatible", "compatibility_confidence",
                         "confirmed", "notes"],
            "properties": {
                "quantity_available": {"type": "integer", "description": "Physical compatible units the supplier can commit."},
                "unit_price": {"type": "number", "description": "Unit price in INR unless the supplier explicitly states another currency."},
                "availability_hours": {"type": "number", "description": "Earliest pickup/delivery time in hours from now."},
                "delivery_method": {"type": "string", "enum": ["pickup", "delivery", "unknown"]},
                "compatible": {"type": "boolean"},
                "compatibility_confidence": {"type": "number", "description": "0 to 1 confidence based only on call evidence."},
                "confirmed": {"type": "boolean", "description": "True only when quantity and timing are explicitly committed."},
                "notes": {"type": "string"}
            },
            "additionalProperties": False
        }

        # The idempotency key is a durable business key: retries of the same supplier
        # leg reuse it instead of creating another provider-side call.
        idem = f"restartai:{request.idempotency_key or request.part_number}:{supplier.name}"
        with CalleClient(api_key=self.api_key, base_url=self.base_url) as client:
            result = client.calls.create_and_wait(
                task=task,
                recipients=[{
                    "phones": [supplier.phone],
                    "region": supplier.region,
                    "locale": supplier.locale
                }],
                result_schema={
                    "type": "object",
                    "required": ["supplier_call_completed"],
                    "properties": {"supplier_call_completed": {"type": "boolean"}},
                    "additionalProperties": False
                },
                recipient_result_schema=schema,
                metadata={"app": "restartai", "supplier": supplier.name, "part": request.part_number},
                idempotency_key=idem
            )

        # Current CALL-E API returns recipient-level structured_result when
        # recipient_result_schema is used.
        recipient_result = {}
        if isinstance(result, dict):
            recipients = result.get("recipients") or []
            if recipients:
                recipient_result = recipients[0].get("structured_result") or {}
            call_id = result.get("call_id") or result.get("id")
        else:
            call_id = getattr(result, "call_id", None) or getattr(result, "id", None)

        if not recipient_result:
            raise RuntimeError("CALL-E completed without a usable structured supplier result.")

        return {
            **recipient_result,
            "supplier": supplier.name,
            "phone": supplier.phone,
            "call_id": call_id,
            "source": "CALL-E live"
        }

    def _build_task(self, supplier, request, known_offers):
        prior = ""
        if known_offers:
            prior = "\nInformation already discovered from other suppliers:\n" + "\n".join(
                f"- {x['supplier']}: {x.get('quantity_available', 0)} units, "
                f"₹{x.get('unit_price', 0)}/unit, {x.get('availability_hours', 999)}h"
                for x in known_offers
            )
        remaining = max(0, request.quantity - sum(
            x.get("quantity_available", 0) for x in known_offers
            if x.get("compatible") and x.get("confirmed")
        ))
        return f"""
You are RestartAI, an emergency production recovery procurement agent.
Call {supplier.name} at the supplied phone number.

The factory needs a compatible replacement part quickly. This is supplier
information gathering only; never authorize a purchase.

Machine: {request.machine}
Required part: {request.part_number} — {request.part_description}
Original required quantity: {request.quantity}
Remaining quantity after prior committed offers: {remaining}
Maximum acceptable recovery time: {request.max_hours} hours
Compatibility notes: {request.compatibility_notes or 'None'}

Ask for the exact compatible part, physically available quantity, unit price,
earliest pickup or delivery time, delivery/pickup method, and whether the
supplier explicitly commits the quantity and timing. Do not invent inventory.
{prior}
""".strip()

    def _demo_call(self, supplier, request, known_offers):
        demo = {
            "Supplier A": dict(quantity_available=20, unit_price=150, availability_hours=24, delivery_method="delivery", compatible=True, compatibility_confidence=.98, confirmed=True, notes="Full stock, arrives tomorrow."),
            "Supplier B": dict(quantity_available=20, unit_price=220, availability_hours=2, delivery_method="delivery", compatible=True, compatibility_confidence=.98, confirmed=True, notes="Full stock, 2-hour delivery."),
            "Supplier C": dict(quantity_available=8, unit_price=180, availability_hours=.5, delivery_method="pickup", compatible=True, compatibility_confidence=.95, confirmed=True, notes="Only 8 available for immediate pickup."),
            "Supplier D": dict(quantity_available=12, unit_price=190, availability_hours=1.5, delivery_method="delivery", compatible=True, compatibility_confidence=.96, confirmed=True, notes="12 available; 90-minute delivery.")
        }
        raw = demo.get(supplier.name, {
            "quantity_available": 0, "unit_price": 0, "availability_hours": 999,
            "delivery_method": "unknown", "compatible": False,
            "compatibility_confidence": 0, "confirmed": False,
            "notes": "No demo data for this supplier."
        })
        status = "FULL STOCK" if raw["quantity_available"] >= request.quantity else ("PARTIAL STOCK" if raw["quantity_available"] > 0 else "NO STOCK")
        return {"supplier": supplier.name, "phone": supplier.phone, **raw,
                "status": status, "currency": "INR",
                "source": "demo", "call_id": f"demo-{supplier.name.lower().replace(' ', '-')}"}
