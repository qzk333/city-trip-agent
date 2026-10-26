"""Bounded handoffs between independently scoped agent contexts."""

from typing import Literal

from pydantic import Field

from app.schemas import GuideResearch, Schema


class GuideFinding(Schema):
    claim: str = Field(min_length=1, max_length=300)
    source_ids: list[str] = Field(min_length=1, max_length=6)
    place_ids: list[str] = Field(default_factory=list, max_length=30)


class GuideAnalysis(Schema):
    summary: str = Field(min_length=1, max_length=600)
    findings: list[GuideFinding] = Field(min_length=1, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=6)


class GuideBrief(Schema):
    status: Literal["ready", "skipped", "unavailable"]
    summary: str = Field(max_length=600)
    findings: list[GuideFinding] = Field(default_factory=list, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=6)


class GuideTaskResult(Schema):
    research: GuideResearch
    brief: GuideBrief
    trace: list[dict] = Field(default_factory=list)

    @property
    def task_status(self) -> str:
        return {"ready": "completed", "skipped": "skipped", "unavailable": "degraded"}[self.brief.status]
