from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

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
class Sample:
    """A fixed, synthetic event that may be triggered from the local demo UI."""

    id: str
    label: str
    source: str | None = None
    event_type: str | None = None
    source_event_id: str | None = None
    event_time_offset_min: int = 0
    milestone_id: str | None = None
    exception_category: str | None = None
    channel: str | None = None
    description: str | None = None
    notes: str | None = None
    details: dict[str, object] = field(default_factory=dict)
    duplicate_of: str | None = None

    def public(self) -> dict[str, object]:
        return {key: value for key, value in {"id": self.id, "label": self.label, "duplicateOf": self.duplicate_of, "eventType": self.event_type}.items() if value is not None}


@dataclass(frozen=True)
class InvalidSample:
    """A deliberately malformed legacy event, limited to the validation demonstration."""

    id: str
    issue: str
    label: str
    source: str
    event_type: str
    description: str
    details: dict[str, object]
    event_time_offset_min: int = 0
    milestone_id: str | None = None
    tracked_entity_id: str | None = None
    event_time_string: bool = False

    def public(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "issue": self.issue}


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
    samples: tuple[Sample, ...] = ()
    invalid_samples: tuple[InvalidSample, ...] = ()
    legacy_references: tuple[str, ...] = ()
    source_contracts: dict[str, dict[str, object]] = field(default_factory=dict)
    impact: dict[str, object] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "title": self.title,
            "entityId": self.entity_id,
            "vocabulary": self.vocabulary,
            "risk": self.risk,
            "sources": self.sources,
            "sourceContracts": self.source_contracts,
            "milestones": sorted(self.milestones),
            "exceptionCategories": {key: asdict(value) for key, value in self.exception_categories.items()},
            "telemetry": self.telemetry.public() if self.telemetry else {"enabled": False},
            "samples": [sample.public() for sample in self.samples],
            "invalidSamples": [sample.public() for sample in self.invalid_samples],
        }


PROFILES = {
    "retail": Profile("retail", "Retail", "Store replenishment shipment", "RTL-SHP-24017", {"entity": "Replenishment shipment", "operator": "Replenishment planner", "destination": "Store"}, {"onTimeToleranceMin": 15, "missedGraceMin": 30, "atRiskWindowMin": 45, "lateArrivalMin": 30}, {"berry_supplier": "api", "carrier": "edi", "dc_wms": "wms_scan", "store_receiving": "manual"}, frozenset({"ready_for_pickup", "departed", "arrived_dc", "out_for_delivery", "received"}), {"missed_pickup": ExceptionCategory("Missed pickup", "high", 240, 3), "delivery_delay": ExceptionCategory("Delivery delay: store replenishment at risk", "high", 120, 3), "label_mismatch": ExceptionCategory("Pallet label mismatch", "low", 480, 1)}),
    "healthcare": Profile("healthcare", "Healthcare", "Temperature-controlled lab supply transfer", "HC-TRF-3307", {"entity": "Supply transfer", "operator": "Logistics coordinator", "destination": "Facility"}, {"onTimeToleranceMin": 10, "missedGraceMin": 20, "atRiskWindowMin": 30, "lateArrivalMin": 20}, {"reagent_supplier": "api", "supply_hub": "wms_scan", "courier": "api", "receiving_lab": "manual"}, frozenset({"released", "dispatched", "handoff", "delivered", "received"}), {"delayed_handoff": ExceptionCategory("Delayed courier handoff", "high", 60, 3), "handling_excursion": ExceptionCategory("Handling condition outside configured range", "critical", 30, 4), "missing_paperwork": ExceptionCategory("Chain-of-custody form missing", "medium", 240, 2)}),
    "manufacturing": Profile("manufacturing", "Manufacturing", "Inbound component lot to production line", "MFG-LOT-8842", {"entity": "Component lot", "operator": "Materials planner", "destination": "Production line"}, {"onTimeToleranceMin": 20, "missedGraceMin": 20, "atRiskWindowMin": 60, "lateArrivalMin": 45}, {"supplier_portal": "edi", "receiving_dock": "wms_scan", "quality_station": "manual", "mes": "api"}, frozenset({"supplier_ready", "inbound_departure", "dock_arrival", "quality_release", "line_side_delivery"}), {"quality_hold": ExceptionCategory("Quality hold", "critical", 90, 4), "production_risk_delay": ExceptionCategory("Delay risks production start", "high", 60, 4), "asn_mismatch": ExceptionCategory("ASN quantity mismatch", "low", 480, 1)}),
}


_TELEMETRY = {
    "retail": Telemetry("temperature", "Reefer trailer temperature (perishable goods)", "°C", 1, 4, "REEFER-TRL-8812", 2.6, 6.8),
    "healthcare": Telemetry("temperature", "Cooler box temperature (controlled supplies)", "°C", 2, 8, "LOGGER-CC-41", 5, 9.4),
    "manufacturing": Telemetry("relative_humidity", "Dock staging humidity (moisture-sensitive components)", "%RH", 30, 60, "DOCK-RH-07", 45, 71),
}

_SOURCE_CONTRACTS = {
    "retail": {"berry_supplier": {"version": 1, "freshnessTargetMin": 30, "requiredCorrelationKeys": ["purchaseOrder", "supplierId"]}},
    "healthcare": {"reagent_supplier": {"version": 1, "freshnessTargetMin": 20, "requiredCorrelationKeys": ["transferOrder", "supplierId"]}},
    "manufacturing": {"supplier_portal": {"version": 1, "freshnessTargetMin": 45, "requiredCorrelationKeys": ["purchaseOrder", "asnId", "supplierId"]}},
}

_IMPACT = {
    "retail": {"label": "Store shelf cover", "consequence": "shelf empty at {destination} during the weekend promo", "criticalShortfallHours": 2, "watchMarginHours": 1},
    "healthcare": {"label": "Lab reagent cover", "consequence": "{destination} runs out of RK-200 kits and postpones scheduled runs", "criticalShortfallHours": 1, "watchMarginHours": 1},
    "manufacturing": {"label": "Line-side material cover", "consequence": "{destination} starves and its work order stops", "criticalShortfallHours": 1, "watchMarginHours": 0.5},
}


_SAMPLES = {
    "retail": (
        Sample("arrived_dc", "Carrier: arrived at cross-dock", "carrier", "milestone", "TMS-ARR-5531", milestone_id="arrived_dc", description="Trailer checked in at cross-dock door 14", notes="Driver reported heavy traffic on I-95", details={"scac": "FRTX", "trailerId": "TRL-8812", "door": "14", "ediStatus": "X3", "raw": "ST*214*0419~B10*TRL8812*RO77821*FRTX~LX*1~AT7*X3*NS***{{date}}*{{time}}*UT~MS1*TRENTON*NJ*US~SE*6*0419~"}),
        Sample("arrived_dc_resend", "Carrier: resend arrival (duplicate)", duplicate_of="arrived_dc"),
        Sample("supplier_contract_gap", "Supplier: update missing purchase-order correlation", "berry_supplier", "status_note", "SUP-MAP-45018", description="Supplier update received without the purchase-order correlation key", notes="Synthetic contract-gap demonstration; no supplier record is changed", details={"supplierId": "SUP-BERRY-44", "etaRevisionMin": 90, "raw": {"type": "supplier.eta_revised", "supplier": "SUP-BERRY-44", "delayMinutes": 90, "sentAt": "{{iso}}"}}),
        Sample("late_departure_scan", "WMS: departure scan uploaded 3h late", "dc_wms", "milestone", "WMS-DEP-9001", -190, "departed", description="Outbound departure scan uploaded after handheld resync", notes="Handheld was offline in the yard", details={"dockDoor": "D7", "handheldId": "HH-22", "palletsScanned": 24, "raw": {"scanner": "HH-22", "action": "SHIP_CONFIRM", "sscc": "(00)106141412345678908", "scannedAt": "{{iso}}", "uploadBatch": "SYNC-0317"}}),
        Sample("delay_alert", "Carrier: ETA slip alert", "carrier", "exception", "TMS-ALERT-771", exception_category="delivery_delay", channel="api", description="Carrier ETA to store slipped 2 hours; delayed replenishment before promo", notes="Traffic incident on I-95", details={"scac": "FRTX", "etaRevisionMin": 120, "reasonCode": "TRAFFIC", "raw": {"type": "shipment.eta_changed", "shipment": "RO77821", "carrier": "FRTX", "delayMinutes": 120, "reason": "TRAFFIC", "sentAt": "{{iso}}"}}),
        Sample("delay_store_report", "Store: reports same delay", "store_receiving", "exception", "STR-NOTE-12", exception_category="delivery_delay", description="Store manager flagged delayed replenishment for chilled berries", notes="Promo end cap is empty", details={"storeId": "STORE-1187", "reportedBy": "store.manager", "shelfGapCases": 60}),
        Sample("out_for_delivery", "Carrier: out for delivery", "carrier", "milestone", "TMS-OFD-5602", milestone_id="out_for_delivery", channel="gps", description="Trailer dispatched from cross-dock to Store #1187 (geofence exit)", details={"scac": "FRTX", "trailerId": "TRL-8812", "stopSeq": 3, "raw": {"device": "TELEMATICS-8812", "fix": "{{iso}}", "lat": 40.2171, "lon": -74.7429, "speedKph": 38, "heading": 212, "geofence": "XDOCK-TRENTON:EXIT"}}),
        Sample("received", "Store: received", "store_receiving", "milestone", "STR-RCV-3391", milestone_id="received", description="Store received chilled berries; 2 cases short", details={"storeId": "STORE-1187", "casesReceived": 478, "shortCases": 2, "receiver": "dock.associate"}),
    ),
    "healthcare": (
        Sample("handoff", "Courier: handoff completed", "courier", "milestone", "CUR-HND-7790", milestone_id="handoff", description="Cooler box CB-17 handed to last-mile courier", notes="Seal intact, custody signed", details={"courierId": "CUR-9", "vehicle": "BIKE-3", "sealId": "SEAL-5541", "raw": {"event": "custody.transferred", "transferOrder": "TO-55021", "from": "CUR-6", "to": "CUR-9", "seal": "SEAL-5541", "at": "{{iso}}"}}),
        Sample("handoff_resend", "Courier: resend handoff (duplicate)", duplicate_of="handoff"),
        Sample("supplier_contract_gap", "Supplier: update missing transfer-order correlation", "reagent_supplier", "status_note", "SUP-MAP-55021", description="Supplier release update received without the transfer-order correlation key", notes="Synthetic contract-gap demonstration; operational data only", details={"supplierId": "SUP-REAGENT-17", "releaseDelayMin": 90, "raw": {"type": "supplier.release_revised", "supplier": "SUP-REAGENT-17", "delayMinutes": 90, "sentAt": "{{iso}}"}}),
        Sample("late_dispatch_scan", "Hub: dispatch scan uploaded 2.5h late", "supply_hub", "milestone", "HUB-DSP-2290", -150, "dispatched", description="Loading bay scan synced after network outage", notes="Bay scanner reconnected", details={"bayId": "LB-2", "scannerId": "SC-8", "raw": {"scanner": "SC-8", "action": "LOAD_OUT", "barcode": "CB-17", "bay": "LB-2", "scannedAt": "{{iso}}", "uploadBatch": "RECONNECT-12"}}),
        Sample("excursion_alert", "Logger: handling excursion alert", "courier", "exception", "CUR-ALERT-355", exception_category="handling_excursion", channel="iot", description="Cooler temperature outside configured 2-8 C range during transit", notes="Box left in direct sun at relay", details={"courierId": "CUR-9", "coolerBoxId": "CB-17", "observedC": 9.4, "raw": {"logger": "LOGGER-CC-41", "ts": "{{iso}}", "tempC": 9.4, "battery": 0.82, "alarm": "HIGH_TEMP", "rangeC": [2, 8]}}),
        Sample("excursion_lab_report", "Lab: reports same excursion", "receiving_lab", "exception", "LAB-NOTE-51", exception_category="handling_excursion", description="Receiving desk notified of handling excursion before arrival", details={"deskId": "RCV-1"}),
        Sample("delivered", "Courier: delivered", "courier", "milestone", "CUR-DLV-7811", milestone_id="delivered", description="Delivered to Northside Facility Lab receiving desk", details={"courierId": "CUR-9", "signature": "captured"}),
        Sample("received", "Lab: received", "receiving_lab", "milestone", "LAB-RCV-60", milestone_id="received", description="Kits checked in and moved to lab cold storage", details={"deskId": "RCV-1", "kitsReceived": 36, "storageUnit": "FRIDGE-4"}),
    ),
    "manufacturing": (
        Sample("quality_hold", "Quality: place lot on hold", "quality_station", "exception", "QS-HOLD-4402", exception_category="quality_hold", description="Quality hold: porosity found on 3 of 20 sampled housings", notes="Awaiting supplier 8D response", details={"inspectionId": "INSP-2207", "defectCode": "POROSITY", "sampleSize": 20, "defects": 3, "raw": {"form": "NCR-INCOMING", "inspector": "qa.tech", "enteredAt": "{{iso}}", "text": "Porosity on bore face, 3/20 samples. Hold lot pending 8D."}}),
        Sample("quality_hold_resend", "Quality: resend hold (duplicate)", duplicate_of="quality_hold"),
        Sample("production_risk", "MES: line start at risk", "mes", "exception", "MES-ALERT-901", exception_category="production_risk_delay", description="Line B start at risk: housings not line-side for B-shift", notes="Material shortage in 60 minutes", details={"lineId": "LINE-B", "workOrder": "WO-7781", "stationId": "ST-04", "minutesToShortage": 60, "raw": {"topic": "mes/line-b/material", "severity": "WARN", "part": "HX-220", "station": "ST-04", "minutesToStarve": 60, "ts": "{{iso}}"}}),
        Sample("late_departure_asn", "Supplier: departure ASN uploaded 6h late", "supplier_portal", "milestone", "SUP-DEP-1199", -365, "inbound_departure", channel="batch_file", description="Supplier re-sent departure update from batch upload", details={"purchaseOrder": "PO-44190", "asnId": "ASN-30018", "supplierId": "SUP-PCL", "batchId": "B-77", "raw": "asn_id,event,event_time,pro\nASN-30018,DEPARTED,{{iso}},PRO-77120"}),
        Sample("supplier_contract_gap", "Supplier: ASN update missing ASN correlation", "supplier_portal", "status_note", "SUP-MAP-30018", description="Supplier update received without the ASN correlation key", notes="Synthetic contract-gap demonstration; no production schedule is changed", details={"purchaseOrder": "PO-44190", "supplierId": "SUP-PCL", "etaRevisionMin": 90, "raw": {"type": "supplier.asn_revised", "purchaseOrder": "PO-44190", "supplier": "SUP-PCL", "delayMinutes": 90, "sentAt": "{{iso}}"}}),
        Sample("quality_release", "Quality: release lot", "quality_station", "milestone", "QS-REL-4410", milestone_id="quality_release", description="Lot released after 100% re-inspection; 3 pieces scrapped", details={"inspectionId": "INSP-2208", "inspector": "qa.tech", "scrapped": 3}),
        Sample("line_side_delivery", "MES: line-side delivery", "mes", "milestone", "MES-LSD-930", milestone_id="line_side_delivery", description="Housings delivered to Line B supermarket", details={"lineId": "LINE-B", "stationId": "ST-04", "kanbanCards": 6}),
    ),
}

_INVALID_SAMPLES = {
    "retail": (
        InvalidSample("legacy_missing_entity", "missing_tracked_entity_id", "Legacy feed: no trackedEntityId", "legacy_edi", "milestone", "EDI 214 status AF received without shipment reference", {"legacyRef": "RO-77821", "ediCode": "AF"}, -60, "arrived_dc"),
        InvalidSample("legacy_string_time", "event_time_is_string", "Legacy feed: eventTime as string", "legacy_edi", "status_note", "EDI status note with text timestamp", {"ediCode": "X6"}, -120, tracked_entity_id="RTL-SHP-24017", event_time_string=True),
    ),
    "healthcare": (
        InvalidSample("legacy_missing_entity", "missing_tracked_entity_id", "Legacy feed: no trackedEntityId", "legacy_fax_import", "milestone", "Scanned courier sheet imported without transfer reference", {"legacyRef": "TO-55021", "pageCount": 1}, -50, "handoff"),
        InvalidSample("legacy_string_time", "event_time_is_string", "Legacy feed: eventTime as string", "legacy_fax_import", "status_note", "Courier sheet note with handwritten time", {"pageCount": 1}, -100, tracked_entity_id="HC-TRF-3307", event_time_string=True),
    ),
    "manufacturing": (
        InvalidSample("legacy_missing_entity", "missing_tracked_entity_id", "Legacy feed: no trackedEntityId", "legacy_csv_upload", "milestone", "Receiving CSV row without lot number", {"legacyRef": "PO-44190", "csvRow": 17}, -100, "dock_arrival"),
        InvalidSample("legacy_string_time", "event_time_is_string", "Legacy feed: eventTime as string", "legacy_csv_upload", "status_note", "Receiving CSV row with text timestamp", {"csvRow": 18}, -80, tracked_entity_id="MFG-LOT-8842", event_time_string=True),
    ),
}

_LEGACY_REFERENCES = {"retail": ("RO-77821",), "healthcare": ("TO-55021",), "manufacturing": ("PO-44190",)}

PROFILES = {
    profile_id: replace(profile, telemetry=_TELEMETRY[profile_id], samples=_SAMPLES[profile_id], invalid_samples=_INVALID_SAMPLES[profile_id], legacy_references=_LEGACY_REFERENCES[profile_id], source_contracts=_SOURCE_CONTRACTS[profile_id], impact=_IMPACT[profile_id])
    for profile_id, profile in PROFILES.items()
}


def get_profile(profile_id: str) -> Profile:
    try:
        return PROFILES[profile_id]
    except KeyError as error:
        raise ValueError(f'Unknown profile "{profile_id}". Expected one of {", ".join(PROFILE_IDS)}.') from error