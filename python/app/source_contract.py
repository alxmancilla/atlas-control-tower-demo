from __future__ import annotations

from .profiles import Profile


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def assess_source_contract(profile: Profile, event: dict) -> dict | None:
    """Return bounded mapping/correlation/freshness evidence for a declared source contract."""
    spec = profile.source_contracts.get(event["source"])
    if not spec:
        return None
    required = list(spec.get("requiredCorrelationKeys", []))
    details = event.get("details") or {}
    correlation = {key: value for key in required if (value := _text(details.get(key)))}
    missing = [key for key in required if key not in correlation]
    lag = (event.get("reliability") or {}).get("lagMinutes")
    target = spec["freshnessTargetMin"]
    freshness = "unknown" if lag is None else "stale" if lag > target else "future_dated" if lag < -5 else "fresh"
    return {
        "contractId": f"{profile.id}:{event['source']}:v{spec['version']}",
        "version": spec["version"],
        "mappingStatus": "incomplete" if missing else "mapped",
        "requiredCorrelationKeys": required,
        "correlation": correlation,
        "missingCorrelationKeys": missing,
        "freshness": {"targetMinutes": target, "observedLagMinutes": lag, "status": freshness},
    }