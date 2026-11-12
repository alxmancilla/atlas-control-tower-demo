"""Python-owned provisioning and synthetic seed data for the non-production demo."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .data_quality import upsert_quality_issues
from .db import COLLECTIONS
from .events import ingest_event
from .exceptions import apply_exception_action
from .impact import recompute_impact
from .profiles import PROFILES, Profile
from .search import create_search_indexes
from .telemetry import synthetic_series
from .validation import build_invalid_event, detect_stage, event_collection_options
from .schemas import EventInput


INDEX_DEFINITIONS = (
    ("entities", [("trackedEntityId", 1)], {"unique": True, "name": "trackedEntityId_unique"}),
    ("entities", [("profile", 1)], {"name": "profile"}),
    ("events", [("trackedEntityId", 1), ("eventTime", 1)], {"name": "entity_eventTime"}),
    ("events", [("trackedEntityId", 1), ("milestoneId", 1), ("eventTime", 1)], {"name": "entity_milestone_eventTime"}),
    ("events", [("idempotencyKey", 1)], {"unique": True, "name": "idempotencyKey_unique", "partialFilterExpression": {"idempotencyKey": {"$type": "string"}}}),
    ("events", [("profile", 1), ("receivedAt", -1)], {"name": "profile_receivedAt"}),
    ("exceptions", [("exceptionId", 1)], {"unique": True, "name": "exceptionId_unique"}),
    ("exceptions", [("exceptionKey", 1)], {"unique": True, "name": "exceptionKey_unique"}),
    ("exceptions", [("profile", 1), ("status", 1), ("slaDueAt", 1)], {"name": "profile_status_sla"}),
    ("exceptions", [("trackedEntityId", 1), ("status", 1)], {"name": "entity_status"}),
    ("quality_issues", [("issueKey", 1)], {"unique": True, "name": "issueKey_unique"}),
    ("quality_issues", [("profile", 1), ("trackedEntityId", 1), ("status", 1), ("severity", -1), ("updatedAt", -1)], {"name": "profile_entity_status_priority"}),
    ("execution_outbox", [("executionKey", 1)], {"unique": True, "name": "executionKey_unique"}),
    ("execution_outbox", [("profile", 1), ("trackedEntityId", 1), ("status", 1), ("updatedAt", -1)], {"name": "profile_entity_status_updated"}),
    ("positions", [("locationId", 1), ("itemRef", 1)], {"unique": True, "name": "location_item_unique"}),
    ("telemetry", [("meta.trackedEntityId", 1), ("meta.metric", 1), ("ts", 1)], {"name": "meta_entity_metric_ts"}),
)

RETIRED_COLLECTIONS = ("telemetry_rollups",)


_SEEDS = {
    "retail": {"entity": {"entityKind": "replenishment_shipment", "product": {"ref": "SKU-55120", "name": "Chilled berries 12x250g", "quantity": 480, "unit": "cases"}, "origin": {"id": "DC-EAST-02", "name": "East Regional DC"}, "destination": {"id": "STORE-1187", "name": "Store #1187 Riverside"}, "metadata": {"replenishmentOrder": "RO-77821", "temperatureClass": "chilled", "promoWindow": "weekend berries promo"}}, "lines": [("RO-77821", "STORE-1187", "Store #1187 Riverside", 180, 0, 60, 20), ("RO-77822", "STORE-1192", "Store #1192 Hillcrest", 160, 45, 110, 18), ("RO-77823", "STORE-1203", "Store #1203 Lakeside", 140, 90, 70, 15)], "milestones": [("ready_for_pickup", "Ready for pickup", "dc_wms", -300, 60), ("departed", "Departed DC", "carrier", -240, 60), ("arrived_dc", "Arrived at cross-dock", "carrier", -45, 240), ("out_for_delivery", "Out for delivery", "carrier", 60, 120), ("received", "Received at store", "store_receiving", 180, 150)], "trace": ("RTL-BERRIES-ETA-01", "Supplier ETA disruption threatens a weekend store promotion", [("supplier", "SUP-BERRY-44", "North Valley Berry Growers", "revised inbound ETA for PO-45018", "late_signal"), ("fulfillment", "DC-EAST-02", "East Regional DC allocation", "released 24 pallets for replenishment order RO-77821", "allocated"), ("transportation", "RTL-SHP-24017", "FRTX chilled replenishment shipment", "carries the allocated berries to three stores", "in_transit"), ("customer", "STORE-1187", "Store #1187 Riverside promo end cap", "first delivery stop; shelf cover is exposed", "at_risk")]), "events": [("berry_supplier", "status_note", "SUP-ETA-45018", -310, -50, "Supplier ETA revision arrived after DC allocation; chilled berries are 4 hours behind plan", None, {"crossDomainTrace": "RTL-BERRIES-ETA-01", "purchaseOrder": "PO-45018", "supplierId": "SUP-BERRY-44", "etaRevisionMin": 240}), ("dc_wms", "milestone", "WMS-RDY-4410", -296, None, "Pallets staged at outbound door D7 and released for pickup", "ready_for_pickup", {"dockDoor": "D7"}), ("carrier", "exception", "TMS-ALERT-702", -250, None, "Carrier missed the scheduled pickup window at East Regional DC", "missed_pickup", {"scac": "FRTX"}), ("carrier", "milestone", "TMS-DEP-5510", -200, None, "Trailer departed East Regional DC with replacement tractor", "departed", {"scac": "FRTX"}), ("dc_wms", "exception", "WMS-EXC-118", -280, None, "Two pallet labels did not match the advance ship notice", "label_mismatch", {"dockDoor": "D7"})], "history": [("RTL-SHP-23110", "missed_pickup", 12, "planner.kim", "Tractor never showed at the outbound dock", "Tendered the load to backup carrier within 40 min"), ("RTL-SHP-23544", "delivery_delay", 8, "planner.ruiz", "Highway closure pushed the store ETA back three hours", "Moved stock from Store #1192 and re-sequenced the route")], "portfolio": [("RTL-SHP-24018", "RO-77824", "STORE-1210", "Store #1210 Midtown", "in_transit", "carrier", -12, "GPS ping: vehicle on route", "none", None), ("RTL-SHP-24019", "RO-77825", "STORE-1226", "Store #1226 Airport", "delayed", "carrier", -38, "EDI 214: delay at cross-dock", "high", ("delivery_delay", 50, "Cross-dock delay threatens morning replenishment window", "planner.lee")), ("RTL-SHP-24020", "RO-77826", "STORE-1231", "Store #1231 Harbor", "at_risk", "dc_wms", -26, "WMS: outbound pallet staged, waiting for carrier", "watch", None)]},
    "healthcare": {"entity": {"entityKind": "facility_supply_transfer", "product": {"ref": "RK-200", "name": "Reagent kit RK-200 (synthetic)", "quantity": 36, "unit": "kits"}, "origin": {"id": "HUB-CENTRAL", "name": "Central Supply Hub"}, "destination": {"id": "LAB-NORTH", "name": "Northside Facility Lab"}, "metadata": {"transferOrder": "TO-55021", "handlingClass": "2-8C controlled", "priorityLane": "same-day"}}, "lines": [("TO-55021", "LAB-NORTH", "Northside Facility Lab", 12, 0, 6, 3), ("TO-55022", "LAB-EAST", "Eastside Clinic Lab", 12, 30, 12, 3), ("TO-55023", "LAB-WEST", "Westside Clinic Lab", 12, 60, 4, 2)], "milestones": [("released", "Released by hub", "supply_hub", -180, 45), ("dispatched", "Dispatched", "courier", -150, 45), ("handoff", "Handoff to last-mile courier", "courier", -40, 120), ("delivered", "Delivered to facility", "courier", 30, 90), ("received", "Received and checked in", "receiving_lab", 60, 45)], "trace": ("HC-REAGENT-RELEASE-01", "Late supplier release threatens temperature-controlled lab replenishment", [("supplier", "SUP-REAGENT-17", "Aster Reagent Supply (synthetic)", "revised release timing for transfer order TO-55021", "late_signal"), ("fulfillment", "HUB-CENTRAL", "Central Supply Hub allocation", "prepared validated cooler box CB-17", "allocated"), ("transportation", "HC-TRF-3307", "Same-day controlled courier transfer", "carries allocated kits through custody handoff", "in_transit"), ("customer", "LAB-NORTH", "Northside Facility Lab receiving desk", "first facility stop; reagent cover is exposed", "at_risk")]), "events": [("reagent_supplier", "status_note", "SUP-REL-55021", -250, -55, "Supplier release revision arrived after hub allocation; RK-200 kits are 3 hours behind plan", None, {"crossDomainTrace": "HC-REAGENT-RELEASE-01", "transferOrder": "TO-55021", "supplierId": "SUP-REAGENT-17", "releaseDelayMin": 180}), ("supply_hub", "milestone", "HUB-REL-2201", -182, None, "Transfer released from Central Supply Hub cold room", "released", {"coolerBoxId": "CB-17"}), ("courier", "milestone", "CUR-DSP-7714", -140, None, "Courier dispatched from hub loading bay", "dispatched", {"courierId": "CUR-6"}), ("courier", "exception", "CUR-ALERT-310", -35, None, "Missed handoff: last-mile courier not at transfer point", "delayed_handoff", {"waitMin": 25}), ("receiving_lab", "exception", "LAB-NOTE-44", -170, None, "Chain-of-custody form not attached to transfer order", "missing_paperwork", {"deskId": "RCV-1"})], "history": [("HC-TRF-3011", "handling_excursion", 15, "coord.osei", "Data logger showed the cooler above 8 C", "Quarantined kits and shipped replacements"), ("HC-TRF-3140", "delayed_handoff", 9, "coord.lind", "Relay courier was stuck at a prior stop", "Dispatched a standby courier")], "portfolio": [("HC-TRF-3308", "TO-55022", "LAB-SOUTH", "Southside Facility Lab", "in_transit", "courier", -9, "Courier GPS: cold-chain transfer on route", "none", None), ("HC-TRF-3309", "TO-55023", "LAB-WEST", "Westside Clinic Lab", "delayed", "courier", -31, "Courier: vehicle reassigned after breakdown", "critical", ("delayed_handoff", 20, "Replacement courier required before reagent stockout", "coordinator.ortiz")), ("HC-TRF-3310", "TO-55024", "LAB-EAST", "Eastside Clinic Lab", "at_risk", "supply_hub", -18, "Supply hub: transfer released to carrier", "watch", None)]},
    "manufacturing": {"entity": {"entityKind": "component_lot", "product": {"ref": "HX-220", "name": "Machined housing HX-220", "quantity": 600, "unit": "pieces"}, "origin": {"id": "SUP-PCL", "name": "Precision Castings Ltd (supplier)"}, "destination": {"id": "PLANT3-LINE-B", "name": "Plant 3, Line B"}, "metadata": {"purchaseOrder": "PO-44190", "workOrder": "WO-7781", "lineStartShift": "B-shift"}}, "lines": [("WO-7781", "PLANT3-LINE-B", "Plant 3, Line B", 250, 0, 120, 100), ("WO-7784", "PLANT3-LINE-C", "Plant 3, Line C", 200, 20, 400, 80), ("WO-7786", "PLANT3-LINE-D", "Plant 3, Line D", 150, 40, 60, 60)], "milestones": [("supplier_ready", "Supplier ready", "supplier_portal", -480, 120), ("inbound_departure", "Inbound departure", "supplier_portal", -360, 120), ("dock_arrival", "Dock arrival", "receiving_dock", -120, 300), ("quality_release", "Quality release", "quality_station", -30, 90), ("line_side_delivery", "Line-side delivery", "mes", 60, 90)], "trace": ("MFG-HX220-ASN-01", "Late ASN revision and quality exposure threaten Line B production start", [("supplier", "SUP-PCL", "Precision Castings Ltd (synthetic)", "revised ASN for purchase order PO-44190", "late_signal"), ("inbound_logistics", "DOCK-3", "Plant 3 receiving dock", "stages the HX-220 lot before inspection", "awaiting_receipt"), ("quality", "INSP-2207", "Incoming quality station", "must release lot before line-side supply", "exposed"), ("manufacturing_execution", "PLANT3-LINE-B", "Plant 3 Line B work order WO-7781", "will starve when material cover expires", "at_risk")]), "events": [("supplier_portal", "status_note", "SUP-ASN-REV-30018", -430, -50, "Supplier ASN revision arrived after production planning; HX-220 inbound ETA slipped 4 hours", None, {"crossDomainTrace": "MFG-HX220-ASN-01", "purchaseOrder": "PO-44190", "asnId": "ASN-30018", "supplierId": "SUP-PCL", "etaRevisionMin": 240}), ("supplier_portal", "milestone", "SUP-RDY-1180", -470, None, "Supplier marked lot packed and ready to ship", "supplier_ready", {"purchaseOrder": "PO-44190", "asnId": "ASN-30018", "supplierId": "SUP-PCL"}), ("supplier_portal", "milestone", "SUP-DEP-1192", -355, None, "Inbound truck departed supplier", "inbound_departure", {"purchaseOrder": "PO-44190", "asnId": "ASN-30018", "supplierId": "SUP-PCL"}), ("receiving_dock", "milestone", "DOCK-ARR-551", -95, None, "Lot received at dock 3 and staged for incoming inspection", "dock_arrival", {"dockId": "DOCK-3"}), ("receiving_dock", "exception", "DOCK-EXC-77", -90, None, "Counted 24 cartons against ASN quantity of 25", "asn_mismatch", {"dockId": "DOCK-3"})], "history": [("MFG-LOT-8519", "quality_hold", 20, "qa.nguyen", "Incoming inspection found casting voids", "Sorted the lot and opened an 8D"), ("MFG-LOT-8650", "production_risk_delay", 11, "planner.berg", "Inbound housings late; line would starve", "Resequenced the line and expedited parts")], "portfolio": [("MFG-LOT-8843", "PO-44191", "PLANT3-LINE-E", "Plant 3, Line E", "in_transit", "supplier_portal", -14, "Supplier ASN: shipment departed", "none", None), ("MFG-LOT-8844", "PO-44192", "PLANT3-LINE-F", "Plant 3, Line F", "delayed", "supplier_portal", -46, "Supplier: port congestion affects pickup", "high", ("production_risk_delay", 35, "Component delay threatens Line F production start", "materials.chen")), ("MFG-LOT-8845", "PO-44193", "PLANT3-LINE-G", "Plant 3, Line G", "at_risk", "receiving_dock", -22, "Yard update: trailer awaiting dock assignment", "watch", None)]},
}


def _at(anchor: datetime, minutes: int) -> datetime:
    return anchor + timedelta(minutes=minutes)


def _entity(profile: Profile, spec: dict, anchor: datetime) -> dict:
    data = spec["entity"]
    lines = [{"orderRef": order, "stop": index, "stopOffsetMin": offset, "destination": {"id": location, "name": name}, "itemRef": data["product"]["ref"], "quantity": quantity, "unit": data["product"]["unit"]} for index, (order, location, name, quantity, offset, _on_hand, _rate) in enumerate(spec["lines"], 1)]
    trace_id, scenario, nodes = spec["trace"]
    return {"trackedEntityId": profile.entity_id, "profile": profile.id, **data, "orderLines": lines, "plannedMilestones": [{"milestoneId": ident, "label": label, "sequence": index, "source": source, "plannedTime": _at(anchor, offset), "maxDwellMin": dwell} for index, (ident, label, source, offset, dwell) in enumerate(spec["milestones"], 1)], "crossDomain": {"traceId": trace_id, "scenario": scenario, "nodes": [{"domain": domain, "id": ident, "label": label, "relation": relation, "state": state} for domain, ident, label, relation, state in nodes]}, "anchorAt": anchor, "demo": {"seeded": True}}


async def _backfill_cross_domain_trace(entity_coll, entity: dict) -> bool:
    """Add the later trace fixture to legacy primary demo entities without replacing data."""
    result = await entity_coll.update_one(
        {"trackedEntityId": entity["trackedEntityId"], "crossDomain": {"$exists": False}},
        {"$set": {"crossDomain": entity["crossDomain"]}},
    )
    return bool(result.modified_count)


async def ensure_setup(db) -> dict:
    cursor = await db.list_collections()
    existing = {item["name"]: item for item in [row async for row in cursor]}
    created = []
    for key, name in COLLECTIONS.items():
        if name in existing:
            continue
        options = {"timeseries": {"timeField": "ts", "metaField": "meta", "granularity": "minutes"}} if key == "telemetry" else {}
        await db.create_collection(name, **options)
        created.append(name)
    if existing.get(COLLECTIONS["telemetry"], {}).get("type") not in {None, "timeseries"}:
        raise ValueError(f"{COLLECTIONS['telemetry']} exists but is not a time-series collection. Reset the demo database to recreate it.")
    for key, fields, options in INDEX_DEFINITIONS:
        await db[COLLECTIONS[key]].create_index(fields, **options)
    await db[COLLECTIONS["state"]].update_one({"_id": "validation"}, {"$setOnInsert": {"stage": "flexible", "history": [], "updatedAt": datetime.now(timezone.utc)}}, upsert=True)
    return {"created": created, "indexes": len(INDEX_DEFINITIONS)}


async def _backfill_quality_issues(db, profile: Profile) -> int:
    """Create work items for accepted events seeded before the quality queue existed."""
    opened = 0
    cursor = db[COLLECTIONS["events"]].find({"profile": profile.id, "reliability": {"$type": "object"}})
    async for event in cursor:
        opened += sum(item["outcome"] == "opened" for item in await upsert_quality_issues(db, profile, event))
    return opened


async def _seed_profile(db, profile: Profile, anchor: datetime) -> dict:
    spec, entity_coll = _SEEDS[profile.id], db[COLLECTIONS["entities"]]
    entity = _entity(profile, spec, anchor)
    inserted = await entity_coll.update_one({"trackedEntityId": profile.entity_id}, {"$setOnInsert": entity}, upsert=True)
    trace_backfilled = await _backfill_cross_domain_trace(entity_coll, entity)
    entity = await entity_coll.find_one({"trackedEntityId": profile.entity_id}, {"anchorAt": 1})
    anchor = entity["anchorAt"]
    data = spec["entity"]
    for order, location, _name, _quantity, _offset, on_hand, consumption in spec["lines"]:
        await db[COLLECTIONS["positions"]].update_one({"locationId": location, "itemRef": data["product"]["ref"]}, {"$setOnInsert": {"profile": profile.id, "locationId": location, "itemRef": data["product"]["ref"], "onHand": on_hand, "consumptionPerHour": consumption, "unit": data["product"]["unit"], "updatedAt": anchor}}, upsert=True)
    events = {"already_present": 0}
    for source, event_type, event_id, offset, received_offset, description, relation, details in sorted(spec["events"], key=lambda item: item[3]):
        key = f"{source}:{event_id}"
        if await db[COLLECTIONS["events"]].count_documents({"idempotencyKey": {"$eq": key, "$type": "string"}}, limit=1):
            events["already_present"] += 1
            continue
        payload = EventInput(source=source, sourceEventId=event_id, eventType=event_type, eventTime=_at(anchor, offset), description=description, milestoneId=relation if event_type == "milestone" else None, exceptionCategory=relation if event_type == "exception" else None, details=details)
        result = await ingest_event(db, profile, payload, received_at=_at(anchor, received_offset if received_offset is not None else offset + 2), seeded=True)
        events[result["outcome"]] = events.get(result["outcome"], 0) + 1
        if relation in {"label_mismatch", "missing_paperwork", "asn_mismatch"}:
            exception = result.get("exception") or {}
            if exception.get("exceptionId"):
                await apply_exception_action(db, exception["exceptionId"], {"action": "acknowledge", "operator": "seed.operator"})
    history_added = 0
    for index, (entity_id, category, days, by, summary, resolution) in enumerate(spec["history"], 1):
        category_spec = profile.exception_categories[category]
        opened = _at(anchor, -days * 1440)
        resolved = _at(opened, min(category_spec.sla_min, 180))
        document = {"exceptionId": f"HIST-{profile.id[:3].upper()}-{index:03}", "exceptionKey": f"{profile.id}:{entity_id}:{category}", "trackedEntityId": entity_id, "profile": profile.id, "category": category, "categoryLabel": category_spec.label, "severity": category_spec.severity, "impact": category_spec.impact, "status": "resolved", "owner": by, "summary": f"{category_spec.label}: {summary}", "triggeringEvent": None, "relatedEventIds": [], "slaDueAt": _at(opened, category_spec.sla_min), "createdAt": opened, "updatedAt": resolved, "resolution": {"note": resolution, "by": by, "at": resolved}, "audit": [{"at": opened, "action": "opened", "by": "history", "note": summary}, {"at": resolved, "action": "resolve", "by": by, "note": resolution}], "demo": {"history": True}}
        result = await db[COLLECTIONS["exceptions"]].update_one({"exceptionKey": document["exceptionKey"]}, {"$setOnInsert": document}, upsert=True)
        history_added += int(bool(result.upserted_id))
    portfolio_entities = portfolio_exceptions = 0
    for entity_id, reference, location, name, state, source, offset, description, level, exception in spec["portfolio"]:
        portfolio = {"trackedEntityId": entity_id, "profile": profile.id, "entityKind": data["entityKind"], "product": data["product"], "origin": data["origin"], "destination": {"id": location, "name": name}, "orderLines": [], "plannedMilestones": [], "metadata": {**data["metadata"], "portfolioReference": reference}, "anchorAt": anchor, "businessImpact": {"level": level, "linesAtRisk": int(level in {"high", "critical"}), "lineCount": 1, "unitsShort": 18 if level == "high" else 6 if level == "critical" else 0, "unit": data["product"]["unit"], "computedAt": anchor}, "portfolio": {"state": state, "lastTrustedSignal": {"source": source, "description": description, "at": _at(anchor, offset)}}, "demo": {"seeded": True, "portfolio": True}}
        result = await entity_coll.update_one({"trackedEntityId": entity_id}, {"$setOnInsert": portfolio}, upsert=True)
        portfolio_entities += int(bool(result.upserted_id))
        if exception:
            category, sla_offset, summary, owner = exception
            category_spec = profile.exception_categories[category]
            document = {"exceptionId": f"PORT-{entity_id}", "exceptionKey": f"{profile.id}:{entity_id}:{category}", "trackedEntityId": entity_id, "profile": profile.id, "category": category, "categoryLabel": category_spec.label, "severity": category_spec.severity, "impact": category_spec.impact, "status": "open", "owner": owner, "summary": summary, "triggeringEvent": None, "relatedEventIds": [], "slaDueAt": _at(anchor, sla_offset), "createdAt": _at(anchor, -30), "updatedAt": anchor, "resolution": None, "businessImpact": portfolio["businessImpact"], "audit": [{"at": _at(anchor, -30), "action": "opened", "by": f"source:{source}", "note": summary}], "demo": {"seeded": True, "portfolio": True}}
            result = await db[COLLECTIONS["exceptions"]].update_one({"exceptionKey": document["exceptionKey"]}, {"$setOnInsert": document}, upsert=True)
            portfolio_exceptions += int(bool(result.upserted_id))
    if not await db[COLLECTIONS["telemetry"]].count_documents({"meta.profile": profile.id}, limit=1):
        await db[COLLECTIONS["telemetry"]].insert_many(synthetic_series(profile, anchor))
    invalid_samples = []
    for sample in profile.invalid_samples:
        exists = await db[COLLECTIONS["events"]].count_documents({"profile": profile.id, "demo.sampleId": sample.id, "demo.seededInvalid": True}, limit=1)
        if exists:
            invalid_samples.append({"id": sample.id, "result": "already present"})
            continue
        document = build_invalid_event(profile, sample, anchor)
        document["demo"]["seededInvalid"] = True
        try:
            await db[COLLECTIONS["events"]].insert_one(document)
            invalid_samples.append({"id": sample.id, "result": "inserted"})
        except Exception:
            invalid_samples.append({"id": sample.id, "result": "rejected by validator"})
    quality_issues = await _backfill_quality_issues(db, profile)
    impact = await recompute_impact(db, profile)
    return {
        "profile": profile.id,
        "entity": "created" if inserted.upserted_id else "existing",
        "crossDomainTrace": "backfilled" if trace_backfilled else "present",
        "portfolio": {"entities": portfolio_entities, "exceptions": portfolio_exceptions},
        "events": events,
        "history": {"added": history_added, "total": len(spec["history"])},
        "qualityIssues": quality_issues,
        "invalidSamples": invalid_samples,
        "businessImpact": impact["level"],
    }


async def setup_demo(db, *, now: datetime | None = None, with_search: bool = True) -> dict:
    setup, anchor = await ensure_setup(db), (now or datetime.now(timezone.utc))
    seeds = [await _seed_profile(db, profile, anchor) for profile in PROFILES.values()]
    search = await create_search_indexes(db) if with_search else []
    return {"setup": setup, "seeds": seeds, "search": search, "validationStage": detect_stage(await event_collection_options(db))}


async def reset_demo(db, confirmation: str, *, now: datetime | None = None, with_search: bool = True) -> dict:
    if confirmation != db.name:
        raise ValueError(f'Reset not confirmed. Type the database name "{db.name}" to confirm.')
    cursor = await db.list_collections()
    names = {item["name"] async for item in cursor}
    dropped = []
    for name in (*COLLECTIONS.values(), *RETIRED_COLLECTIONS):
        if name == COLLECTIONS["state"] or name not in names:
            continue
        await db.drop_collection(name)
        dropped.append(name)
    if COLLECTIONS["state"] in names:
        await db[COLLECTIONS["state"]].delete_many({"_id": {"$ne": "maintenance"}})
    return {"dropped": dropped, **(await setup_demo(db, now=now, with_search=with_search))}