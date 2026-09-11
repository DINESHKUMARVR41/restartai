from app.recovery_engine import RecoveryEngine
from app.call_e_service import CalleService
from app.models import RecoveryRequest, Supplier

def test_split_plan_is_selected():
    engine = RecoveryEngine(CalleService())
    request = RecoveryRequest(
        machine="CNC",
        part_number="SKF 6205-2RS",
        part_description="bearing",
        quantity=20,
        max_hours=3,
        downtime_cost_per_hour=5000,
        suppliers=[
            Supplier(name="Supplier A", phone="+1"),
            Supplier(name="Supplier B", phone="+2"),
            Supplier(name="Supplier C", phone="+3"),
            Supplier(name="Supplier D", phone="+4"),
        ],
    )
    data = engine.start(request)
    data = engine.replan(data["run_id"])
    assert data["recommended_plan"] is not None
    assert data["recommended_plan"]["recovery_time_hours"] <= 3
    assert sum(x["quantity"] for x in data["recommended_plan"]["legs"]) == 20
