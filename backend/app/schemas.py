from datetime import date, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(Schema):
    name: str = Field(min_length=1, max_length=100)
    longitude: float = Field(ge=119, le=121)
    latitude: float = Field(ge=29, le=31)
    coordinate_system: Literal["GCJ-02"] = "GCJ-02"


class Appointment(Schema):
    place_id: str
    day: int = Field(default=1, ge=1, le=3)
    start: time
    end: time

    @model_validator(mode="after")
    def ordered(self):
        if any(t.second or t.microsecond or t.tzinfo is not None for t in (self.start, self.end)):
            raise ValueError("时间请按北京时间填写到分钟，不带时区偏移")
        if self.end <= self.start:
            raise ValueError("预约结束时间必须晚于开始时间")
        return self


class Requirements(Schema):
    city: Literal["杭州"] = "杭州"
    start_date: date
    days: int = Field(default=1, ge=1, le=3)
    day_start: time = time(9)
    day_end: time = time(18)
    hotel: Location = Field(
        default_factory=lambda: Location(
            name="演示起点 · 湖滨",
            longitude=120.163,
            latitude=30.251,
        )
    )
    transport: Literal["transit", "walking"] = "transit"
    pace: Literal["relaxed", "balanced"] = "relaxed"
    interests: list[Literal["nature", "culture", "coffee", "city"]] = Field(
        default_factory=lambda: ["nature", "culture"],
        max_length=4,
    )
    must_visit: list[str] = Field(default_factory=list, max_length=12)
    appointments: list[Appointment] = Field(default_factory=list, max_length=6)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def valid_window(self):
        if any(t.second or t.microsecond or t.tzinfo is not None for t in (self.day_start, self.day_end)):
            raise ValueError("时间请按北京时间填写到分钟，不带时区偏移")
        if self.day_end <= self.day_start:
            raise ValueError("每日结束时间必须晚于开始时间；初版不支持跨夜日程")
        if len(self.must_visit) != len(set(self.must_visit)):
            raise ValueError("必去地点不能重复")
        if any(a.day > self.days for a in self.appointments):
            raise ValueError("预约日期超出旅行天数")
        return self


class Place(Schema):
    id: str
    name: str
    subtitle: str
    longitude: float
    latitude: float
    coordinate_system: Literal["GCJ-02"] = "GCJ-02"
    category: Literal["nature", "culture", "coffee", "city"]
    indoor: bool
    duration_minutes: int = Field(gt=0)
    opening: str | None
    closing: str | None
    description: str
    source_url: str
    evidence_note: str
    fixture: bool = True


class ToolResult(Schema):
    status: Literal["ok", "partial", "no_result", "unavailable", "invalid_input"]
    data: dict[str, Any] = Field(default_factory=dict)
    provider: str
    observed_at: str
    evidence: str
    error: str | None = None


class RouteLeg(Schema):
    origin_id: str
    destination_id: str
    duration_minutes: int = Field(ge=1)
    distance_meters: int = Field(ge=0)
    walking_meters: int | None = Field(default=None, ge=0)
    provider: str
    estimated: bool = True
    evidence: str


class Activity(Schema):
    id: str
    place_id: str
    day: int
    start: str
    end: str
    locked: bool = False
    reason: str


class DayPlan(Schema):
    day: int
    date: str
    activities: list[Activity]
    legs: list[RouteLeg]


class Check(Schema):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    activity_id: str | None = None


class Itinerary(Schema):
    status: Literal["FEASIBLE", "NEEDS_CONFIRMATION", "CONFLICT", "NO_SOLUTION_WITHIN_LIMIT"]
    days: list[DayPlan]
    checks: list[Check] = Field(default_factory=list)
    weather: ToolResult
    summary: str = ""
    trace: list[dict[str, Any]] = Field(default_factory=list)
    planning_mode: str
    tool_provider: str
    day_start_offsets: dict[str, int] = Field(default_factory=dict)


class ProposalRequest(Schema):
    base_version: int = Field(ge=0)
    action: Literal["delay", "lock", "unlock", "remove", "indoor"]
    activity_id: str | None = None
    day: int = Field(default=1, ge=1, le=3)
    delay_minutes: int = Field(default=40, ge=5, le=180)


class ApplyRequest(Schema):
    base_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=100)


class RestoreRequest(ApplyRequest):
    pass
