from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(min_length=1, max_length=100)
    channel: str | None = Field(default=None, max_length=50)
    source_event_id: str = Field(alias="sourceEventId", min_length=1, max_length=200)
    event_type: str = Field(alias="eventType", min_length=1, max_length=100)
    event_time: datetime = Field(alias="eventTime")
    description: str = Field(min_length=1, max_length=500)
    milestone_id: str | None = Field(default=None, alias="milestoneId", max_length=100)
    exception_category: str | None = Field(default=None, alias="exceptionCategory", max_length=100)
    notes: str | None = Field(default=None, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)


class ExceptionActionInput(BaseModel):
    action: str = Field(min_length=1, max_length=50)
    owner: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)
    operator: str | None = Field(default=None, max_length=500)


class RecommendationDecisionInput(BaseModel):
    action_id: str = Field(alias="actionId", min_length=1, max_length=100)
    decision: str = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=500)
    operator: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(populate_by_name=True)


class TelemetryReadingInput(BaseModel):
    kind: str = Field(min_length=1, max_length=20)


class SampleEventInput(BaseModel):
    sample_id: str = Field(alias="sampleId", min_length=1, max_length=100)

    model_config = ConfigDict(populate_by_name=True)


class ValidationStageInput(BaseModel):
    stage: str = Field(min_length=1, max_length=20)


class ValidationSampleInput(BaseModel):
    sample_id: str = Field(alias="sampleId", min_length=1, max_length=100)

    model_config = ConfigDict(populate_by_name=True)


class DocumentIdInput(BaseModel):
    id: str = Field(min_length=1, max_length=64)