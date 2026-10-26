from datetime import date, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(Schema):
    name: str = Field(min_length=1, max_length=100)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    coordinate_system: Literal["GCJ-02", "WGS84"] = "GCJ-02"


class Appointment(Schema):
    place_id: str
    day: int = Field(default=1, ge=1, le=3)
    start: time
    end: time

    @model_validator(mode="after")
    def ordered(self):
        if any(t.second or t.microsecond or t.tzinfo is not None for t in (self.start, self.end)):
            raise ValueError("请按目的地当地时间填写到分钟，不带时区偏移")
        if self.end <= self.start:
            raise ValueError("预约结束时间必须晚于开始时间")
        return self


class TravelBoundary(Schema):
    location: Location
    time: time
    buffer_minutes: int = Field(default=45, ge=0, le=240)

    @model_validator(mode="after")
    def minute_precision(self):
        if self.time.second or self.time.microsecond or self.time.tzinfo is not None:
            raise ValueError("抵离时间请使用目的地当地时间，精确到分钟。")
        return self


class DailyWindow(Schema):
    start: time
    end: time

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start or any(
            t.second or t.microsecond or t.tzinfo for t in (self.start, self.end)
        ):
            raise ValueError("每日时段须为当地分钟时间，结束晚于开始。")
        return self


class Requirements(Schema):
    city: str = Field(default="杭州", min_length=1, max_length=100)
    country_code: str = Field(default="CN", pattern="^[A-Z]{2}$")
    country: str = "中国"
    state_id: str = Field(default="CN-ZJ", min_length=1, max_length=30)
    province: str = "浙江省"
    city_id: int | None = None
    timezone: str = "Asia/Shanghai"
    catalog_id: str | None = Field(default=None, max_length=32)
    start_date: date
    days: int = Field(default=1, ge=1, le=3)
    day_start: time = time(9)
    day_end: time = time(18)
    lodging_mode: Literal["fixed", "route_first"] = "fixed"
    arrival: TravelBoundary | None = None
    departure: TravelBoundary | None = None
    day_windows: dict[str, DailyWindow] = Field(default_factory=dict)
    place_days: dict[str, int] = Field(default_factory=dict, max_length=30)
    hotel: Location = Field(
        default_factory=lambda: Location(
            name="演示起点 · 湖滨",
            longitude=120.163,
            latitude=30.251,
        )
    )
    transport: Literal["auto", "transit", "walking"] = "auto"
    use_web_guides: bool = True
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
            raise ValueError("请按目的地当地时间填写到分钟，不带时区偏移")
        if self.day_end <= self.day_start:
            raise ValueError("每日结束时间必须晚于开始时间；初版不支持跨夜日程")
        if len(self.must_visit) != len(set(self.must_visit)):
            raise ValueError("必去地点不能重复")
        if any(a.day > self.days for a in self.appointments):
            raise ValueError("预约日期超出旅行天数")
        if any(key not in {str(day) for day in range(1, self.days + 1)} for key in self.day_windows):
            raise ValueError("每日时段的日期超出旅行天数。")
        if any(day < 1 or day > self.days for day in self.place_days.values()):
            raise ValueError("指定地点的日期超出旅行天数。")
        return self


class Place(Schema):
    id: str
    name: str
    subtitle: str
    longitude: float
    latitude: float
    coordinate_system: Literal["GCJ-02", "WGS84"] = "GCJ-02"
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
    mode: Literal["walking", "transit", "driving", "unknown"] = "unknown"
    recommendation: str = ""
    source_ids: list[str] = Field(default_factory=list)
    route_status: Literal["queried", "estimated"] = "estimated"


class GuideSource(Schema):
    id: str
    title: str
    url: str
    excerpt: str = ""
    content_kind: Literal["search_excerpt", "page_excerpt"] = "search_excerpt"


class TransportAdvice(Schema):
    place_ids: list[str] = Field(min_length=1, max_length=30)
    mode: Literal["walking", "transit", "driving"]
    advice: str = Field(min_length=1, max_length=400)
    source_ids: list[str] = Field(min_length=1, max_length=6)


class GuideResearch(Schema):
    status: Literal["ok", "partial", "unavailable", "not_requested"] = "not_requested"
    query: str = ""
    fetched_at: str = ""
    sources: list[GuideSource] = Field(default_factory=list)
    advice: list[TransportAdvice] = Field(default_factory=list)
    note: str = "尚未检索网上攻略。"


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


class HotelCandidate(Schema):
    id: str = Field(min_length=1, max_length=100)
    location: Location
    address: str = Field(default="", max_length=300)
    provider: Literal["amap", "demo", "user"]
    source_url: str = ""
    observed_at: str = ""
    evening_distance_meters: int = Field(ge=0)
    morning_distance_meters: int = Field(ge=0)
    evening_minutes: int = Field(ge=0)
    morning_minutes: int = Field(ge=0)
    commute_kind: Literal["distance_estimate"] = "distance_estimate"
    reason: str = ""
    price_note: str = "未接入入住日期报价，房价、房型、库存及取消政策请在预订平台核实。"


class HotelNight(Schema):
    night: int = Field(ge=1, le=2)
    checkin: str
    checkout: str
    last_place_id: str
    next_place_id: str
    candidates: list[HotelCandidate] = Field(default_factory=list, max_length=8)
    selected_id: str | None = None
    note: str = ""


class HotelResearch(Schema):
    status: Literal["not_requested", "not_needed", "ready", "partial", "unavailable"] = "not_requested"
    nights: list[HotelNight] = Field(default_factory=list, max_length=2)
    note: str = "尚未调研住宿。"
    analysis_status: Literal["not_requested", "ok", "unavailable"] = "not_requested"


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
    guides: GuideResearch = Field(default_factory=GuideResearch)
    lodging: HotelResearch = Field(default_factory=HotelResearch)
    revision_note: str = ""
    revision_status: Literal["not_requested", "model_reviewed", "rules_only", "unavailable"] = "not_requested"
    revision_rounds: int = Field(default=0, ge=0, le=3)
    revision_changes: list[str] = Field(default_factory=list, max_length=40)


class ProposalRequest(Schema):
    base_version: int = Field(ge=0)
    action: Literal["delay", "lock", "unlock", "remove", "indoor"]
    activity_id: str | None = None
    day: int = Field(default=1, ge=1, le=3)
    delay_minutes: int = Field(default=40, ge=5, le=180)


class ApplyRequest(Schema):
    base_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=100)


class HotelChoiceRequest(Schema):
    base_version: int = Field(ge=1)
    night: int = Field(ge=1, le=2)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=100)
    custom_location: Location | None = None

    @model_validator(mode="after")
    def one_choice(self):
        if (self.candidate_id is None) == (self.custom_location is None):
            raise ValueError("请选择一个候选酒店，或填写已订酒店的位置。")
        return self


class NaturalRevisionRequest(Schema):
    base_version: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=1500)
    scope: Literal["local", "related_days", "all_days"] | None = None
    reselect: bool = False


class LocationSearchRequest(Schema):
    query: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    center: Location | None = None


class RestoreRequest(ApplyRequest):
    pass


class CityQuery(Schema):
    city_id: int


class ConnectionTest(Schema):
    service: Literal["model", "amap"]
