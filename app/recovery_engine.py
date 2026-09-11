import uuid
from itertools import combinations
from .models import RecoveryRequest, Offer, RecoveryPlan

RUNS = {}


class RecoveryEngine:
    def __init__(self, calle_service):
        self.calle = calle_service

    def start(self, request: RecoveryRequest):
        run_id = str(uuid.uuid4())[:8]
        offers = []

        # MVP deliberately calls the first three suppliers. Replanning can call the fourth.
        for supplier in request.suppliers[:3]:
            data = self.calle.call_supplier(supplier, request, offers)
            offers.append(Offer(**data).model_dump())

        RUNS[run_id] = {
            "run_id": run_id,
            "request": request.model_dump(),
            "offers": offers,
            "plans": [],
            "stage": "initial_calls"
        }
        return self._snapshot(run_id)

    def replan(self, run_id):
        state = RUNS[run_id]
        request = RecoveryRequest(**state["request"])

        # The interesting agentic action: partial stock triggers another supplier call.
        remaining = request.quantity - sum(
            o["quantity_available"] for o in state["offers"] if o["compatible"] and o["confirmed"]
        )
        # Avoid treating over-supply as remaining demand.
        remaining = max(0, remaining)

        if remaining > 0 and len(request.suppliers) > 3:
            data = self.calle.call_supplier(request.suppliers[3], request, state["offers"])
            state["offers"].append(Offer(**data).model_dump())

        state["plans"] = [p.model_dump() for p in self._generate_plans(request, state["offers"])]
        state["stage"] = "replanned"
        return self._snapshot(run_id)

    def approve(self, run_id):
        state = RUNS[run_id]
        state["stage"] = "human_approved"
        return self._snapshot(run_id)

    def get(self, run_id):
        return self._snapshot(run_id) if run_id in RUNS else None

    def _generate_plans(self, request, offers):
        usable = [Offer(**o) for o in offers if o["compatible"] and o["confirmed"] and o["quantity_available"] > 0]
        plans = []

        for r in range(1, min(3, len(usable)) + 1):
            for combo in combinations(usable, r):
                total_qty = sum(x.quantity_available for x in combo)
                if total_qty < request.quantity:
                    continue

                # Build a simple deterministic allocation.
                remaining = request.quantity
                legs = []
                for x in sorted(combo, key=lambda z: z.availability_hours):
                    q = min(remaining, x.quantity_available)
                    legs.append({
                        "supplier": x.supplier,
                        "quantity": q,
                        "unit_price": x.unit_price,
                        "arrival_hours": x.availability_hours,
                        "delivery_method": x.delivery_method,
                        "compatibility_confidence": x.compatibility_confidence
                    })
                    remaining -= q
                    if remaining == 0:
                        break

                if remaining > 0:
                    continue

                recovery = max(x["arrival_hours"] for x in legs)
                purchase = sum(x["quantity"] * x["unit_price"] for x in legs)
                downtime = request.downtime_cost_per_hour * recovery
                confidence = min(x["compatibility_confidence"] for x in legs)

                plans.append(RecoveryPlan(
                    legs=legs,
                    total_purchase_cost=round(purchase, 2),
                    recovery_time_hours=recovery,
                    downtime_exposure=round(downtime, 2),
                    total_exposure=round(purchase + downtime, 2),
                    compatibility_confidence=confidence,
                    feasible=recovery <= request.max_hours
                ))

        return sorted(plans, key=lambda p: (not p.feasible, p.total_exposure, p.recovery_time_hours))[:5]

    def _snapshot(self, run_id):
        state = RUNS[run_id]
        return {
            "run_id": run_id,
            "stage": state["stage"],
            "request": state["request"],
            "offers": state["offers"],
            "plans": state["plans"],
            "recommended_plan": state["plans"][0] if state["plans"] else None
        }
