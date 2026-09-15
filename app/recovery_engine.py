import asyncio
import json
import logging
import uuid
from itertools import combinations
from pathlib import Path
from .models import RecoveryRequest, Offer, PlanLeg, RecoveryPlan

RUNS = {}
IDEMPOTENCY = {}
logger = logging.getLogger(__name__)


class RecoveryEngine:
    def __init__(self, calle_service):
        self.calle = calle_service
        self.maintenance = self._load_maintenance()

    def _load_maintenance(self):
        path = Path(__file__).resolve().parent.parent / "data" / "maintenance_tasks.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _maintenance_match(self, part_number, part_description):
        text = f"{part_number} {part_description}".lower()
        for task, info in self.maintenance.items():
            if any(k.lower() in text for k in info["keywords"]):
                logger.info("Maintenance task requested=%s found=yes technician_data=%s", text, bool(info.get("required_technician")))
                return task, info
        logger.info("Maintenance task requested=%s found=no technician_data=no", text)
        return "unknown", {"required_technicians": 1, "installation_minutes": 30, "configured": False}

    def _maintenance_info(self, request):
        return self._maintenance_match(request.part_number, request.part_description)

    @staticmethod
    def _maintenance_state(task_name, task_info, required_techs, installation, available_techs):
        configured = task_name != "unknown" and task_info.get("configured", True)
        required_capability = task_info.get("required_technician") if configured else None
        installation_required = task_info.get("installation_required") if configured else None
        knowledge_base_available = task_info.get("technicians_available") if configured else None
        effective_available = available_techs if available_techs is not None else knowledge_base_available
        if not configured:
            status = "not configured"
        elif effective_available is None:
            status = "not configured"
        elif effective_available >= required_techs:
            status = "available"
        else:
            status = "unavailable"
        return {
            "required": required_capability,
            "required_count": required_techs if configured else None,
            "installation_required": installation_required,
            "installation_minutes": installation if configured else None,
            "technicians_available": effective_available if configured else None,
            "status": status,
            "source": "Demo maintenance knowledge base" if configured else "Not configured",
        }

    def maintenance_intelligence(self, part_number, part_description, available_technicians=3, required_technicians_override=None, installation_minutes_override=None):
        task_name, task_info = self._maintenance_match(part_number, part_description)
        configured = task_name != "unknown" and task_info.get("configured", True) and bool(task_info.get("required_technician"))
        required_techs = required_technicians_override if configured and required_technicians_override is not None else task_info.get("required_technicians") if configured else None
        installation = installation_minutes_override if configured and installation_minutes_override is not None else task_info.get("installation_minutes") if configured else None
        state = self._maintenance_state(task_name, task_info, required_techs, installation, available_technicians)
        return {
            "configured": configured,
            "task": task_name,
            "required_technician": state["required"],
            "required_technicians": state["required_count"],
            "installation_required": state["installation_required"],
            "installation_minutes": state["installation_minutes"],
            "technicians_available": state["technicians_available"],
            "status": state["status"],
            "source": state["source"],
        }

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
        technician_intelligence = self.maintenance_intelligence(request.part_number, request.part_description, request.available_technicians, request.required_technicians_override, request.installation_minutes_override)

        state = {
            "run_id": run_id, "request": request.model_dump(),
            "maintenance": {"task": task_name, "required_technicians": required_techs, "installation_minutes": installation, "source": technician_intelligence["source"], "technician_intelligence": technician_intelligence},
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

    async def start_async(self, request: RecoveryRequest):
        if self.calle.mode != "live":
            return self.start(request)
        if not request.live_confirmed:
            raise ValueError("LIVE mode requires explicit confirmation before initiating phone calls.")
        if not request.suppliers:
            raise ValueError("At least one supplier is required for live recovery.")
        key = request.idempotency_key
        if key and key in IDEMPOTENCY:
            return self._snapshot(IDEMPOTENCY[key])

        run_id = str(uuid.uuid4())[:8]
        task_name, task_info = self._maintenance_info(request)
        required_techs = request.required_technicians_override if request.required_technicians_override is not None else task_info["required_technicians"]
        installation = request.installation_minutes_override if request.installation_minutes_override is not None else task_info["installation_minutes"]
        tech = self.maintenance_intelligence(request.part_number, request.part_description, request.available_technicians, request.required_technicians_override, request.installation_minutes_override)
        state = {"run_id": run_id, "request": request.model_dump(), "maintenance": {"task": task_name, "required_technicians": required_techs, "installation_minutes": installation, "source": tech["source"], "technician_intelligence": tech}, "offers": [], "plans": [], "stage": "calling_supplier", "next_supplier_index": 0, "approved": False, "calls": [], "call_failures": []}

        # Live calls are sequential and shortage-driven. Supplier B is not called
        # unless Supplier A leaves a real shortage of confirmed compatible stock.
        for index, supplier in enumerate(request.suppliers):
            remaining = self._remaining_quantity(state["offers"], request.quantity)
            if remaining <= 0:
                break
            await self._call_and_store_async(state, request, index)
            state["next_supplier_index"] = index + 1
            logger.info("LIVE recovery run=%s supplier=%s remaining=%s", run_id, supplier.name, self._remaining_quantity(state["offers"], request.quantity))

        call_ids = {o.get("call_id") for o in state["offers"] if o.get("call_id")}
        state["calls"] = [{k: v for k, v in record.items() if k != "provider_response"} for record in self.calle.calls.values() if record.get("call_id") in call_ids]
        state["stage"] = "recovery_ready" if self._remaining_quantity(state["offers"], request.quantity) <= 0 else "shortfall_unresolved"
        state["plans"] = [p.model_dump() for p in self._generate_plans(request, state["offers"], state["maintenance"])]
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

    async def _call_and_store_async(self, state, request, index, existing_offers=None):
        supplier = request.suppliers[index]
        try:
            data = await self.calle.call_supplier_async(supplier, request, existing_offers if existing_offers is not None else state["offers"], idempotency_key=f"restartai-{state['run_id']}-{index}")
            offer = Offer(**data)
        except Exception as exc:
            call_id = getattr(exc, "call_id", None)
            record = self.calle.calls.get(call_id or "", {})
            diagnostics = getattr(exc, "diagnostics", None) or record.get("diagnostics", {})
            category = diagnostics.get("category")
            human_message = diagnostics.get("human_message")
            notes = human_message or str(exc)
            offer = Offer(supplier=supplier.name, phone=supplier.phone, status="SUPPLIER UNAVAILABLE" if category == "supplier_unavailable" else "CALL FAILED", notes=notes, source="CALL-E LIVE CALL", call_id=call_id or record.get("call_id"))
            state.setdefault("call_failures", []).append({"supplier": supplier.name, "phone": supplier.phone, "call_id": call_id or record.get("call_id"), "category": category, "human_message": human_message, "failure_code": diagnostics.get("failure_code"), "failure_message": diagnostics.get("failure_message"), "attempt_failure_code": diagnostics.get("attempt_failure_code"), "attempt_failure_message": diagnostics.get("attempt_failure_message"), "recipient_status": diagnostics.get("recipient_status"), "attempt_status": diagnostics.get("attempt_status")})
        offer_dict = offer.model_dump()
        if existing_offers is None:
            # Sequential (replan) path: mutate shared state directly as before.
            state["offers"].append(offer_dict)
            state["calls"] = list(self.calle.calls.values())
        return offer_dict

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
            "source": "demo maintenance knowledge base",
            "technician_intelligence": self.maintenance_intelligence(request.part_number, request.part_description, request.available_technicians, request.required_technicians_override, request.installation_minutes_override)
        }
        state["plans"] = [p.model_dump() for p in self._generate_plans(request, state["offers"], state["maintenance"])]
        state["stage"] = "replanned"
        return self._snapshot(run_id)

    async def replan_async(self, run_id):
        if self.calle.mode != "live":
            return self.replan(run_id)
        if run_id not in RUNS:
            raise KeyError("Run not found")
        state = RUNS[run_id]
        request = RecoveryRequest(**state["request"])
        # In LIVE mode every supplier was already called in parallel at start, so
        # next_supplier_index is already at len(suppliers) and this block is a no-op.
        # It only still fires for the (rare) case a run has suppliers added later.
        if state["next_supplier_index"] < len(request.suppliers):
            remaining = self._remaining_quantity(state["offers"], request.quantity)
            if remaining > 0:
                await self._call_and_store_async(state, request, state["next_supplier_index"])
                state["next_supplier_index"] += 1
        task_name, task_info = self._maintenance_info(request)
        required_techs = request.required_technicians_override if request.required_technicians_override is not None else task_info["required_technicians"]
        installation = request.installation_minutes_override if request.installation_minutes_override is not None else task_info["installation_minutes"]
        technician_intelligence = self.maintenance_intelligence(request.part_number, request.part_description, request.available_technicians, request.required_technicians_override, request.installation_minutes_override)
        state["maintenance"] = {"task": task_name, "required_technicians": required_techs, "installation_minutes": installation, "source": technician_intelligence["source"], "technician_intelligence": technician_intelligence}
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
                    required_technician_capability=maintenance.get("technician_intelligence", {}).get("required_technician"),
                    installation_required=maintenance.get("technician_intelligence", {}).get("installation_required"),
                    technician_status=maintenance.get("technician_intelligence", {}).get("status", "not configured"),
                    feasible=not reasons,
                    infeasibility_reasons=reasons
                ))
        return sorted(plans, key=lambda p: (not p.feasible, p.total_exposure, p.recovery_time_hours))[:8]

    def _snapshot(self, run_id):
        state = RUNS[run_id]
        offers = state["offers"]
        safe_offers = [{**offer, "phone": self.calle.mask_phone(offer["phone"])} for offer in offers]
        safe_request = {**state["request"], "suppliers": [{**supplier, "phone": self.calle.mask_phone(supplier["phone"])} for supplier in state["request"]["suppliers"]]}
        return {
            "run_id": run_id, "stage": state["stage"], "request": safe_request,
            "maintenance": state["maintenance"], "offers": safe_offers, "plans": state["plans"],
            "technician_intelligence": state["maintenance"].get("technician_intelligence", {}),
            "recommended_plan": state["plans"][0] if state["plans"] else None,
            "approved": state["approved"], "mode": self.calle.mode,
            "calls": state.get("calls", []),
            "call_failures": state.get("call_failures", []),
            "next_supplier_available": state["next_supplier_index"] < len(RecoveryRequest(**state["request"]).suppliers)
        }
