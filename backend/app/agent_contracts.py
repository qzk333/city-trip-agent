"""Bounded handoffs between independently scoped agent contexts."""

from typing import Literal

from pydantic import Field

from app.schemas import GuideResearch, Schema


class GuideFinding(Schema):
    claim: str = Field(min_length=1, max_length=300)
    source_ids: list[str] = Field(min_length=1, max_length=6)
    place_ids: list[str] = Field(default_factory=list, max_length=30)


class GuidePlaceSuggestion(Schema):
    name: str = Field(min_length=1, max_length=80)
    category: Literal["nature", "culture", "coffee", "city"] = "city"
    reason: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=6)


class GuidePlaceResolution(Schema):
    name: str
    status: Literal[
        "existing", "added", "ambiguous", "no_result", "unsupported", "excluded", "skipped", "unavailable"
    ]
    place_ids: list[str] = Field(default_factory=list, max_length=3)
    source_ids: list[str] = Field(default_factory=list, max_length=6)
    note: str = Field(default="", max_length=300)


class GuideAnalysis(Schema):
    summary: str = Field(min_length=1, max_length=600)
    findings: list[GuideFinding] = Field(min_length=1, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=6)
    place_suggestions: list[GuidePlaceSuggestion] = Field(default_factory=list, max_length=6)


class GuideBrief(Schema):
    status: Literal["ready", "skipped", "unavailable"]
    summary: str = Field(max_length=600)
    findings: list[GuideFinding] = Field(default_factory=list, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=6)
    place_suggestions: list[GuidePlaceSuggestion] = Field(default_factory=list, max_length=6)
    place_resolutions: list[GuidePlaceResolution] = Field(default_factory=list, max_length=6)


class GuideTaskResult(Schema):
    research: GuideResearch
    brief: GuideBrief
    trace: list[dict] = Field(default_factory=list)

    @property
    def task_status(self) -> str:
        return {"ready": "completed", "skipped": "skipped", "unavailable": "degraded"}[self.brief.status]
