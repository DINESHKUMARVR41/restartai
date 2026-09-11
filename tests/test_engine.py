from app.recovery_engine import RecoveryEngine, RUNS, IDEMPOTENCY
from app.call_e_service import CalleService
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
