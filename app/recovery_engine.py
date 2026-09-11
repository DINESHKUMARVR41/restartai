import json
import uuid
from itertools import combinations
from pathlib import Path
from .models import RecoveryRequest, Offer, PlanLeg, RecoveryPlan

RUNS = {}
IDEMPOTENCY = {}


class RecoveryEngine:
    def __init__(self, calle_service):
        self.calle = calle_service
        self.maintenance = self._load_maintenance()

    def _load_maintenance(self):
        path = Path(__file__).resolve().parent.parent / "data" / "maintenance_tasks.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _maintenance_info(self, request):
        text = f"{request.part_number} {request.part_description}".lower()
        for task, info in self.maintenance.items():
            if any(k.lower() in text for k in info["keywords"]):
                return task, info
        return "unknown", {"required_technicians": 1, "installation_minutes": 30}

    def start(self, request: RecoveryRequest):
        if self.calle.mode == "live" and not request.live_confirmed:
            raise ValueError("LIVE mode requires explicit confirmation before initiating phone calls.")
        key = request.idempotency_key
        if key and key in IDEMPOTENCY:
            return self._snapshot(IDEMPOTENCY[key])

        run_id = str(uuid.uuid4())[:8]
        task_name, task_info = self._maintenance_info(request)
        required_techs = request.required_technicians_override if request.required_technicians_override is not None else task_info["required_technicians"]
        installation = request.installation_minutes_override if request.installation_minutes_override is not None else task_info["installation_minutes"]

        state = {
            "run_id": run_id, "request": request.model_dump(),
            "maintenance": {"task": task_name, "required_technicians": required_techs, "installation_minutes": installation, "source": "demo maintenance knowledge base" if task_name != "unknown" else "fallback demo value"},
            "offers": [], "plans": [], "stage": "initial_calls", "next_supplier_index": 0,
            "approved": False
        }
        # Initial wave: at most 3. Later waves are driven by shortfall.
        initial = min(3, len(request.suppliers))
        for i in range(initial):
            self._call_and_store(state, request, i)
        state["next_supplier_index"] = initial
        RUNS[run_id] = state
        if key:
            IDEMPOTENCY[key] = run_id
        return self._snapshot(run_id)

    def _call_and_store(self, state, request, index):
        supplier = request.suppliers[index]
        try:
            data = self.calle.call_supplier(supplier, request, state["offers"])
            offer = Offer(**data)
        except Exception as exc:
            offer = Offer(supplier=supplier.name, phone=supplier.phone, status="CALL FAILED", notes=str(exc), source="call-e")
        state["offers"].append(offer.model_dump())

    def replan(self, run_id):
        if run_id not in RUNS:
            raise KeyError("Run not found")
        state = RUNS[run_id]
        request = RecoveryRequest(**state["request"])

        # Keep discovering suppliers only while a feasible quantity may still be missing.
        while state["next_supplier_index"] < len(request.suppliers):
            remaining = self._remaining_quantity(state["offers"], request.quantity)
            has_partial = any(
                o["compatible"] and o["confirmed"] and 0 < o["quantity_available"] < request.quantity
                for o in state["offers"]
            )
            # A partial offer is valuable information even when another supplier
            # can cover the whole order: another leg may produce a cheaper/faster
            # recovery plan. This is the core adaptive behavior.
            if remaining <= 0 and not has_partial:
                break
            self._call_and_store(state, request, state["next_supplier_index"])
            state["next_supplier_index"] += 1
            # One supplier per replan action makes the adaptive behavior visible.
            break

        task_name, task_info = self._maintenance_info(request)
        state["maintenance"] = {
            "task": task_name,
            "required_technicians": request.required_technicians_override if request.required_technicians_override is not None else task_info["required_technicians"],
            "installation_minutes": request.installation_minutes_override if request.installation_minutes_override is not None else task_info["installation_minutes"],
            "source": "demo maintenance knowledge base"
        }
        state["plans"] = [p.model_dump() for p in self._generate_plans(request, state["offers"], state["maintenance"])]
        state["stage"] = "replanned"
        return self._snapshot(run_id)

    def approve(self, run_id):
        if run_id not in RUNS:
            raise KeyError("Run not found")
        state = RUNS[run_id]
        if not state["plans"]:
            raise ValueError("No recovery plan is available to approve.")
        if not state["plans"][0]["feasible"]:
            raise ValueError("The recommended plan is not feasible and cannot be approved.")
        state["approved"] = True
        state["stage"] = "human_approved"
        return self._snapshot(run_id)

    def get(self, run_id):
        return self._snapshot(run_id) if run_id in RUNS else None

    @staticmethod
    def _remaining_quantity(offers, required):
        return max(0, required - sum(o["quantity_available"] for o in offers if o["compatible"] and o["confirmed"]))

    def _generate_plans(self, request, offers, maintenance):
        usable = [Offer(**o) for o in offers if o["compatible"] and o["confirmed"] and o["quantity_available"] > 0]
        plans = []
        # Exhaustive combinations are small for this MVP and allow true split planning.
        for r in range(1, len(usable) + 1):
            for combo in combinations(usable, r):
                if sum(x.quantity_available for x in combo) < request.quantity:
                    continue
                remaining = request.quantity
                legs = []
                for x in sorted(combo, key=lambda z: z.availability_hours):
                    q = min(remaining, x.quantity_available)
                    legs.append(PlanLeg(
                        supplier=x.supplier, quantity=q, unit_price=x.unit_price,
                        arrival_hours=x.availability_hours, delivery_method=x.delivery_method,
                        compatibility_confidence=x.compatibility_confidence
                    ))
                    remaining -= q
                    if remaining == 0:
                        break
                if remaining:
                    continue
                material_arrival = max(x.arrival_hours for x in legs)
                installation_minutes = int(maintenance["installation_minutes"])
                recovery = material_arrival + installation_minutes / 60
                purchase = sum(x.quantity * x.unit_price for x in legs)
                downtime = request.downtime_cost_per_hour * recovery
                reasons = []
                if maintenance["required_technicians"] > request.available_technicians:
                    reasons.append(f"Technician shortage: {maintenance['required_technicians']} required, {request.available_technicians} available")
                if recovery > request.max_hours:
                    reasons.append(f"Recovery time {recovery:.2f}h exceeds maximum {request.max_hours:.2f}h")
                plans.append(RecoveryPlan(
                    legs=legs, total_purchase_cost=round(purchase,2),
                    material_arrival_hours=round(material_arrival,2),
                    installation_minutes=installation_minutes,
                    recovery_time_hours=round(recovery,2),
                    downtime_exposure=round(downtime,2),
                    total_exposure=round(purchase+downtime,2),
                    compatibility_confidence=min(x.compatibility_confidence for x in legs),
                    required_technicians=maintenance["required_technicians"],
                    available_technicians=request.available_technicians,
                    feasible=not reasons,
                    infeasibility_reasons=reasons
                ))
        return sorted(plans, key=lambda p: (not p.feasible, p.total_exposure, p.recovery_time_hours))[:8]

    def _snapshot(self, run_id):
        state = RUNS[run_id]
        offers = state["offers"]
        return {
            "run_id": run_id, "stage": state["stage"], "request": state["request"],
            "maintenance": state["maintenance"], "offers": offers, "plans": state["plans"],
            "recommended_plan": state["plans"][0] if state["plans"] else None,
            "approved": state["approved"], "mode": self.calle.mode,
            "next_supplier_available": state["next_supplier_index"] < len(RecoveryRequest(**state["request"]).suppliers)
        }
