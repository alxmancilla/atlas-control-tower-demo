"""Conversions for MongoDB values returned through JSON API routes."""
from __future__ import annotations

from bson import ObjectId


def json_safe(value: object) -> object:
    """Recursively convert BSON-only values while retaining the API document shape."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value