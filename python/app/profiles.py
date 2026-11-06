from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from .config import PROFILE_IDS


@dataclass(frozen=True)
class ExceptionCategory:
    label: str
    severity: str
    sla_min: int
    impact: int


@dataclass(frozen=True)
class Telemetry:
    metric: str
    label: str
    unit: str
    minimum: float
    maximum: float
    sensor_id: str
    baseline: float
    excursion_value: float

    def public(self) -> dict[str, object]:
        return {"enabled": True, "metric": self.metric, "label": self.label, "unit": self.unit, "min": self.minimum, "max": self.maximum, "sensorId": self.sensor_id, "baseline": self.baseline, "excursionValue": self.excursion_value}


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    title: str
    entity_id: str
    vocabulary: dict[str, str]
    risk: dict[str, int]
    sources: dict[str, str]
    milestones: frozenset[str]
    exception_categories: dict[str, ExceptionCategory]
    telemetry: Telemetry | None = None

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "title": self.title,
            "entityId": self.entity_id,
            "vocabulary": self.vocabulary,
            "risk": self.risk,
            "sources": self.sources,
            "milestones": sorted(self.milestones),
            "exceptionCategories": {key: asdict(value) for key, value in self.exception_categories.items()},
            "telemetry": self.telemetry.public() if self.telemetry else {"enabled": False},
        }


PROFILES = {
    "retail": Profile("retail", "Retail", "Store replenishment shipment", "RTL-SHP-24017", {"entity": "Replenishment shipment", "operator": "Replenishment planner", "destination": "Store"}, {"onTimeToleranceMin": 15, "missedGraceMin": 30, "atRiskWindowMin": 45, "lateArrivalMin": 30}, {"carrier": "edi", "dc_wms": "wms_scan", "store_receiving": "manual"}, frozenset({"ready_for_pickup", "departed", "arrived_dc", "out_for_delivery", "received"}), {"missed_pickup": ExceptionCategory("Missed pickup", "high", 240, 3), "delivery_delay": ExceptionCategory("Delivery delay: store replenishment at risk", "high", 120, 3), "label_mismatch": ExceptionCategory("Pallet label mismatch", "low", 480, 1)}),
    "healthcare": Profile("healthcare", "Healthcare", "Temperature-controlled lab supply transfer", "HC-TRF-3307", {"entity": "Supply transfer", "operator": "Logistics coordinator", "destination": "Facility"}, {"onTimeToleranceMin": 10, "missedGraceMin": 20, "atRiskWindowMin": 30, "lateArrivalMin": 20}, {"supply_hub": "wms_scan", "courier": "api", "receiving_lab": "manual"}, frozenset({"released", "dispatched", "handoff", "delivered", "received"}), {"delayed_handoff": ExceptionCategory("Delayed courier handoff", "high", 60, 3), "handling_excursion": ExceptionCategory("Handling condition outside configured range", "critical", 30, 4), "missing_paperwork": ExceptionCategory("Chain-of-custody form missing", "medium", 240, 2)}),
    "manufacturing": Profile("manufacturing", "Manufacturing", "Inbound component lot to production line", "MFG-LOT-8842", {"entity": "Component lot", "operator": "Materials planner", "destination": "Production line"}, {"onTimeToleranceMin": 20, "missedGraceMin": 20, "atRiskWindowMin": 60, "lateArrivalMin": 45}, {"supplier_portal": "edi", "receiving_dock": "wms_scan", "quality_station": "manual", "mes": "api"}, frozenset({"supplier_ready", "inbound_departure", "dock_arrival", "quality_release", "line_side_delivery"}), {"quality_hold": ExceptionCategory("Quality hold", "critical", 90, 4), "production_risk_delay": ExceptionCategory("Delay risks production start", "high", 60, 4), "asn_mismatch": ExceptionCategory("ASN quantity mismatch", "low", 480, 1)}),
}


_TELEMETRY = {
    "retail": Telemetry("temperature", "Reefer trailer temperature (perishable goods)", "°C", 1, 4, "REEFER-TRL-8812", 2.6, 6.8),
    "healthcare": Telemetry("temperature", "Cooler box temperature (controlled supplies)", "°C", 2, 8, "LOGGER-CC-41", 5, 9.4),
    "manufacturing": Telemetry("relative_humidity", "Dock staging humidity (moisture-sensitive components)", "%RH", 30, 60, "DOCK-RH-07", 45, 71),
}
PROFILES = {profile_id: replace(profile, telemetry=_TELEMETRY[profile_id]) for profile_id, profile in PROFILES.items()}


def get_profile(profile_id: str) -> Profile:
    try:
        return PROFILES[profile_id]
    except KeyError as error:
        raise ValueError(f'Unknown profile "{profile_id}". Expected one of {", ".join(PROFILE_IDS)}.') from error