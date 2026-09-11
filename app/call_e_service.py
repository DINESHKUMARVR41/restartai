import os
from typing import Any
from .models import Supplier, RecoveryRequest

class CalleService:
    def __init__(self):
        self.mode = os.getenv("CALL_E_MODE", "demo").lower()
        self.api_key = os.getenv("CALLE_API_KEY", "")
        self.base_url = os.getenv("CALLE_BASE_URL", "https://api.heycall-e.com")

    def call_supplier(self, supplier: Supplier, request: RecoveryRequest, known_offers=None) -> dict[str, Any]:
        if self.mode != "live":
            return self._demo_call(supplier, request, known_offers or [])

        if not self.api_key:
            raise RuntimeError("CALLE_API_KEY is missing while CALL_E_MODE=live")

        # Official CALL-E Python SDK. The key remains server-side.
        from calle import CalleClient

        task = self._build_task(supplier, request, known_offers or [])
        schema = {
            "type": "object",
            "required": [
                "quantity_available",
                "unit_price",
                "availability_hours",
                "delivery_method",
                "compatible",
                "compatibility_confidence",
                "confirmed",
                "notes"
            ],
            "properties": {
                "quantity_available": {"type": "integer"},
                "unit_price": {"type": "number"},
                "availability_hours": {"type": "number"},
                "delivery_method": {"type": "string"},
                "compatible": {"type": "boolean"},
                "compatibility_confidence": {"type": "number"},
                "confirmed": {"type": "boolean"},
                "notes": {"type": "string"}
            }
        }

        with CalleClient(api_key=self.api_key, base_url=self.base_url) as client:
            result = client.calls.create_and_wait(
                task=task,
                recipients=[{
                    "phones": [supplier.phone],
                    "region": supplier.region,
                    "locale": supplier.locale
                }],
                result_schema={"type": "object", "properties": {"supplier_call_completed": {"type": "boolean"}}},
                recipient_result_schema=schema,
                metadata={"app": "restartai", "supplier": supplier.name, "part": request.part_number},
                idempotency_key=f"restartai:{request.part_number}:{supplier.name}:{request.quantity}"
            )

        recipient_result = {}
        recipients = result.get("recipients") or []
        if recipients:
            recipient_result = recipients[0].get("structured_result") or {}

        return {
            **recipient_result,
            "call_id": result.get("call_id"),
            "supplier": supplier.name,
            "phone": supplier.phone
        }

    def _build_task(self, supplier, request, known_offers):
        prior = ""
        if known_offers:
            prior = "\nKnown supplier information already gathered:\n" + "\n".join(
                f"- {x['supplier']}: {x.get('quantity_available', 0)} units, ₹{x.get('unit_price', 0)}/unit, "
                f"{x.get('availability_hours', 999)}h"
                for x in known_offers
            )

        return f"""
You are RestartAI, an emergency production recovery procurement agent.
Call {supplier.name} at the supplied phone number.

Goal: determine whether the supplier can help restore a machine quickly.

Machine: {request.machine}
Required part: {request.part_number} — {request.part_description}
Required quantity: {request.quantity}
Maximum acceptable recovery time: {request.max_hours} hours
Compatibility notes: {request.compatibility_notes or 'None'}

Ask for:
1. exact compatible part / equivalent,
2. quantity physically available now,
3. unit price,
4. earliest pickup or delivery time,
5. delivery/pickup method,
6. whether the quantity and timing can be committed.

Do not authorize a purchase. Do not invent inventory.
Return structured facts only after the conversation.
{prior}
""".strip()

    def _demo_call(self, supplier, request, known_offers):
        demo = {
            "Supplier A": dict(quantity_available=20, unit_price=150, availability_hours=24, delivery_method="delivery", compatible=True, compatibility_confidence=.98, confirmed=True, notes="Full stock, arrives tomorrow."),
            "Supplier B": dict(quantity_available=20, unit_price=220, availability_hours=2, delivery_method="delivery", compatible=True, compatibility_confidence=.98, confirmed=True, notes="Full stock, 2-hour delivery."),
            "Supplier C": dict(quantity_available=8, unit_price=180, availability_hours=.5, delivery_method="pickup", compatible=True, compatibility_confidence=.95, confirmed=True, notes="Only 8 available for immediate pickup."),
            "Supplier D": dict(quantity_available=12, unit_price=190, availability_hours=1.5, delivery_method="delivery", compatible=True, compatibility_confidence=.96, confirmed=True, notes="12 available; 90-minute delivery.")
        }
        return {"supplier": supplier.name, "phone": supplier.phone, **demo.get(supplier.name, {
            "quantity_available": 0, "unit_price": 0, "availability_hours": 999,
            "delivery_method": "unknown", "compatible": False,
            "compatibility_confidence": 0, "confirmed": False,
            "notes": "No demo data for this supplier."
        }), "call_id": f"demo-{supplier.name.lower().replace(' ', '-')}"}
