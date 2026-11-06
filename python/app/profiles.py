from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import PROFILE_IDS


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    title: str
    entity_id: str
    vocabulary: dict[str, str]
    risk: dict[str, int]

    def public(self) -> dict[str, object]:
        return asdict(self)


PROFILES = {
    "retail": Profile("retail", "Retail", "Store replenishment shipment", "RTL-SHP-24017", {"entity": "Replenishment shipment", "operator": "Replenishment planner", "destination": "Store"}, {"onTimeToleranceMin": 15, "missedGraceMin": 30, "atRiskWindowMin": 45}),
    "healthcare": Profile("healthcare", "Healthcare", "Temperature-controlled lab supply transfer", "HC-TRF-3307", {"entity": "Supply transfer", "operator": "Logistics coordinator", "destination": "Facility"}, {"onTimeToleranceMin": 10, "missedGraceMin": 20, "atRiskWindowMin": 30}),
    "manufacturing": Profile("manufacturing", "Manufacturing", "Inbound component lot to production line", "MFG-LOT-8842", {"entity": "Component lot", "operator": "Materials planner", "destination": "Production line"}, {"onTimeToleranceMin": 20, "missedGraceMin": 45, "atRiskWindowMin": 60}),
}


def get_profile(profile_id: str) -> Profile:
    try:
        return PROFILES[profile_id]
    except KeyError as error:
        raise ValueError(f'Unknown profile "{profile_id}". Expected one of {", ".join(PROFILE_IDS)}.') from error