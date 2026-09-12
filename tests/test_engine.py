from app.recovery_engine import RecoveryEngine, RUNS, IDEMPOTENCY
import asyncio
from app.call_e_service import CallEError, CalleService
from app.models import RecoveryRequest, Supplier

def request(**kwargs):
    base = dict(
        machine="CNC", part_number="SKF 6205-2RS", part_description="bearing",
        quantity=20, max_hours=3, downtime_cost_per_hour=5000,
        available_technicians=3,
        suppliers=[Supplier(name=f"Supplier {c}", phone=f"+{i}") for i,c in enumerate("ABCD",1)]
    )
    base.update(kwargs)
    return RecoveryRequest(**base)

def setup_function():
    RUNS.clear(); IDEMPOTENCY.clear()

def test_single_supplier_full_stock():
    engine=RecoveryEngine(CalleService())
    r=request(suppliers=[Supplier(name="Supplier B", phone="+2")])
    d=engine.start(r); d=engine.replan(d["run_id"])
    assert d["recommended_plan"]["legs"][0]["quantity"] == 20
    assert d["recommended_plan"]["feasible"]

def test_partial_supplier_plus_second_supplier():
    engine=RecoveryEngine(CalleService())
    d=engine.start(request())
    d=engine.replan(d["run_id"])
    p=d["recommended_plan"]
    assert sum(x["quantity"] for x in p["legs"]) == 20
    assert {x["supplier"] for x in p["legs"]} == {"Supplier C","Supplier D"}

def test_supplier_combination_does_not_overbuy():
    engine=RecoveryEngine(CalleService())
    d=engine.start(request()); d=engine.replan(d["run_id"])
    p=d["recommended_plan"]
    assert sum(x["quantity"] for x in p["legs"]) == 20

def test_no_feasible_supplier():
    engine=RecoveryEngine(CalleService())
    r=request(quantity=100, suppliers=[Supplier(name="Supplier C", phone="+3")])
    d=engine.start(r); d=engine.replan(d["run_id"])
    assert d["recommended_plan"] is None

def test_technician_shortage():
    engine=RecoveryEngine(CalleService())
    r=request(available_technicians=1)
    d=engine.start(r); d=engine.replan(d["run_id"])
    assert d["recommended_plan"]["feasible"] is False
    assert "Technician shortage" in d["recommended_plan"]["infeasibility_reasons"][0]

def test_recovery_time_includes_installation():
    engine=RecoveryEngine(CalleService())
    r=request(installation_minutes_override=30)
    d=engine.start(r); d=engine.replan(d["run_id"])
    assert d["recommended_plan"]["material_arrival_hours"] == 1.5
    assert d["recommended_plan"]["recovery_time_hours"] == 2.0

def test_economic_exposure():
    engine=RecoveryEngine(CalleService())
    d=engine.start(request()); d=engine.replan(d["run_id"])
    assert d["recommended_plan"]["total_purchase_cost"] == 3720
    assert d["recommended_plan"]["downtime_exposure"] == 10000
    assert d["recommended_plan"]["total_exposure"] == 13720

def test_best_plan_selection():
    engine=RecoveryEngine(CalleService())
    d=engine.start(request()); d=engine.replan(d["run_id"])
    assert d["recommended_plan"]["legs"][0]["supplier"] == "Supplier C"

def test_max_recovery_constraint():
    engine=RecoveryEngine(CalleService())
    r=request(max_hours=1)
    d=engine.start(r); d=engine.replan(d["run_id"])
    assert d["recommended_plan"]["feasible"] is False
    assert any("exceeds maximum" in x for x in d["recommended_plan"]["infeasibility_reasons"])

def test_demo_requires_no_api_key():
    service=CalleService()
    assert service.mode == "demo"
    assert service.call_supplier(Supplier(name="Supplier C", phone="+3"), request())["quantity_available"] == 8

def test_human_approval():
    engine=RecoveryEngine(CalleService())
    d=engine.start(request()); d=engine.replan(d["run_id"])
    d=engine.approve(d["run_id"])
    assert d["approved"] is True
    assert d["stage"] == "human_approved"

def test_idempotency_reuses_run():
    engine=RecoveryEngine(CalleService())
    r=request(idempotency_key="incident-123")
    a=engine.start(r); b=engine.start(r)
    assert a["run_id"] == b["run_id"]


def test_technician_intelligence_loads_from_maintenance_data():
    engine = RecoveryEngine(CalleService())
    data = engine.start(request())
    technician = data["technician_intelligence"]
    assert technician["required_technician"] == "Mechanical Maintenance Technician"
    assert technician["installation_required"] is True
    assert technician["installation_minutes"] == 30
    assert technician["technicians_available"] == 3
    assert technician["status"] == "available"


def test_technician_override_takes_precedence_for_availability():
    engine = RecoveryEngine(CalleService())
    data = engine.start(request(available_technicians=0, required_technicians_override=1))
    technician = data["technician_intelligence"]
    assert technician["required_technicians"] == 1
    assert technician["technicians_available"] == 0
    assert technician["status"] == "unavailable"


def test_unknown_maintenance_task_is_not_configured():
    engine = RecoveryEngine(CalleService())
    data = engine.start(request(part_number="unlisted-part", part_description="unlisted component"))
    technician = data["technician_intelligence"]
    assert technician["status"] == "not configured"
    assert technician["required_technician"] is None
    assert technician["technicians_available"] is None


def test_maintenance_intelligence_bearing_endpoint_shape():
    engine = RecoveryEngine(CalleService())
    intelligence = engine.maintenance_intelligence("6205", "Bearing 6205-2RS", 3)
    assert intelligence == {
        "configured": True,
        "task": "bearing_replacement",
        "required_technician": "Mechanical Maintenance Technician",
        "required_technicians": 2,
        "installation_required": True,
        "installation_minutes": 30,
        "technicians_available": 3,
        "status": "available",
        "source": "Demo maintenance knowledge base",
    }


def test_maintenance_intelligence_shortage_and_override():
    engine = RecoveryEngine(CalleService())
    assert engine.maintenance_intelligence("6205", "Bearing 6205-2RS", 1)["status"] == "unavailable"
    overridden = engine.maintenance_intelligence("6205", "Bearing 6205-2RS", 3, required_technicians_override=4)
    assert overridden["required_technicians"] == 4
    assert overridden["status"] == "unavailable"


def test_maintenance_intelligence_motor_and_unknown():
    engine = RecoveryEngine(CalleService())
    motor = engine.maintenance_intelligence("", "Motor replacement", 3)
    assert motor["task"] == "motor_replacement"
    assert motor["required_technicians"] == 3
    assert motor["installation_minutes"] == 120
    assert motor["required_technician"] == "Mechanical Maintenance Technician"
    unknown = engine.maintenance_intelligence("", "Something completely unknown", 3)
    assert unknown["configured"] is False
    assert unknown["required_technician"] is None
    assert unknown["installation_minutes"] is None
    assert unknown["technicians_available"] is None
    assert unknown["status"] == "not configured"


def test_recovery_snapshot_matches_maintenance_intelligence():
    engine = RecoveryEngine(CalleService())
    request_data = request(part_number="6205", part_description="Bearing 6205-2RS")
    snapshot = engine.start(request_data)
    expected = engine.maintenance_intelligence("6205", "Bearing 6205-2RS", 3)
    assert snapshot["technician_intelligence"] == expected


def test_live_supplier_unavailable_is_not_confirmed_and_next_supplier_can_succeed():
    service = CalleService()
    service.mode = "live"
    engine = RecoveryEngine(service)
    r = request(idempotency_key="live-failure", live_confirmed=True, suppliers=[Supplier(name="Supplier A", phone="+919876543210"), Supplier(name="Supplier B", phone="+919876543211")])

    async def call_supplier(supplier, request, known_offers, idempotency_key=None):
        if supplier.name == "Supplier A":
            diagnostics = {"category": "supplier_unavailable", "human_message": "Supplier did not answer or was unavailable.", "failure_code": "call_failed", "failure_message": "NO ANSWER", "attempt_failure_code": "480", "attempt_failure_message": "Not available"}
            raise CallEError("NO ANSWER", code="call_failed", call_id="call_a", diagnostics=diagnostics)
        return {"supplier": supplier.name, "phone": supplier.phone, "quantity_available": 20, "unit_price": 100, "currency": "INR", "availability_hours": 1, "delivery_method": "delivery", "compatible": True, "confirmed": True, "compatibility_confidence": 1, "status": "FULL STOCK", "source": "CALL-E LIVE CALL", "call_id": "call_b"}

    service.call_supplier_async = call_supplier
    d = asyncio.run(engine.start_async(r))
    assert d["offers"][0]["status"] == "SUPPLIER UNAVAILABLE"
    assert d["offers"][0]["confirmed"] is False
    assert d["call_failures"][0]["attempt_failure_code"] == "480"
    assert d["offers"][1]["confirmed"] is True
    assert d["recommended_plan"]["legs"][0]["supplier"] == "Supplier B"
    assert d["agent_actions"] == ["Supplier A could not be reached. Continuing with the next supplier."]


def test_live_all_failed_has_no_recovery_plan():
    service = CalleService()
    service.mode = "live"
    engine = RecoveryEngine(service)
    r = request(idempotency_key="all-failed", live_confirmed=True, suppliers=[Supplier(name="Supplier A", phone="+919876543210"), Supplier(name="Supplier B", phone="+919876543211")])

    async def call_supplier(supplier, request, known_offers, idempotency_key=None):
        raise CallEError("NO ANSWER", code="call_failed", call_id=f"call-{supplier.name[-1]}", diagnostics={"category": "supplier_unavailable", "human_message": "Supplier did not answer or was unavailable.", "failure_code": "call_failed", "failure_message": "NO ANSWER"})

    service.call_supplier_async = call_supplier
    d = asyncio.run(engine.start_async(r))
    d = asyncio.run(engine.replan_async(d["run_id"]))
    assert d["recommended_plan"] is None
    assert all(o["confirmed"] is False for o in d["offers"])
