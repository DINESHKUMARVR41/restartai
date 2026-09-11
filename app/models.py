from typing import Optional, List
from pydantic import BaseModel, Field


class Supplier(BaseModel):
    name: str
    phone: str
    region: str = "IN"
    locale: str = "en-IN"


class RecoveryRequest(BaseModel):
    machine: str
    part_number: str
    part_description: str
    quantity: int = Field(gt=0)
    max_hours: float = Field(gt=0)
    downtime_cost_per_hour: float = Field(ge=0)
    compatibility_notes: str = ""
    suppliers: List[Supplier]


class Offer(BaseModel):
    supplier: str
    phone: str
    quantity_available: int = 0
    unit_price: float = 0
    availability_hours: float = 999
    delivery_method: str = "unknown"
    compatible: bool = False
    compatibility_confidence: float = 0
    confirmed: bool = False
    call_id: Optional[str] = None
    notes: str = ""


class PlanLeg(BaseModel):
    supplier: str
    quantity: int
    unit_price: float
    arrival_hours: float
    delivery_method: str
    compatibility_confidence: float


class RecoveryPlan(BaseModel):
    legs: List[PlanLeg]
    total_purchase_cost: float
    recovery_time_hours: float
    downtime_exposure: float
    total_exposure: float
    compatibility_confidence: float
    feasible: bool
