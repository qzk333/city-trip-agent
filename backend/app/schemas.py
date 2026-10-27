from datetime import date, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.legacy_preferences import upgrade_preferences


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


class TravelProfile(Schema):
    companions: Literal["unspecified", "solo", "friends", "parents", "children"] = "unspecified"
    themes: list[
        Literal["history", "architecture", "night", "photo", "nature", "food", "coffee", "culture", "city"]
    ] = Field(default_factory=list, max_length=9)
    walking: Literal["normal", "less"] = "normal"
    excluded_keywords: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def bounded_keywords(self):
        self.themes = list(dict.fromkeys(self.themes))
        if any(not word.strip() or len(word) > 40 for word in self.excluded_keywords):
            raise ValueError("排除关键词须为 1～40 个字符")
        return self


class AttributeEvidence(Schema):
    field: str = Field(max_length=50)
    value: str = Field(max_length=300)
    kind: Literal["observed", "user_confirmed", "inferred", "unknown"] = "unknown"
    source_url: str = Field(default="", max_length=1000)
    observed_at: str = ""
    excerpt: str = Field(default="", max_length=400)


class PlaceTraits(Schema):
    activity_types: list[str] = Field(default_factory=list, max_length=10)
    themes: list[str] = Field(default_factory=list, max_length=9)
    indoor_outdoor: Literal["indoor", "outdoor", "mixed", "unknown"] = "unknown"
    duration_min: int | None = Field(default=None, ge=5, le=1440)
    duration_max: int | None = Field(default=None, ge=5, le=1440)
    audience_notes: list[str] = Field(default_factory=list, max_length=5)
    unknowns: list[str] = Field(default_factory=list, max_length=8)
    evidence: list[AttributeEvidence] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def ordered_duration(self):
        if self.duration_min and self.duration_max and self.duration_min > self.duration_max:
            raise ValueError("建议停留时长范围无效")
        return self


class CandidateMatch(Schema):
    score: float
    relevance: float
    diversity_penalty: float
    detour_penalty: float
    reasons: list[str] = Field(default_factory=list, max_length=6)


class ThemeRequest(Schema):
    profile: TravelProfile = Field(default_factory=TravelProfile)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_preferences(cls, value):
        return upgrade_preferences(value)


class Requirements(ThemeRequest):
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
    must_visit: list[str] = Field(default_factory=list)
    appointments: list[Appointment] = Field(default_factory=list)
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
    traits: PlaceTraits = Field(default_factory=PlaceTraits)
    candidate_match: CandidateMatch | None = None


class CandidateRequest(ThemeRequest):
    city_id: int
    start_date: date
    days: int = Field(default=1, ge=1, le=3)
    pace: Literal["relaxed", "balanced"] = "relaxed"
    note: str = Field(default="", max_length=1000)
    catalog_id: str | None = Field(default=None, max_length=32)
    keep_ids: list[str] = Field(default_factory=list, max_length=30)
    source_trip_id: str | None = Field(default=None, max_length=32)
    base_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def source_version(self):
        if (self.source_trip_id is None) != (self.base_version is None):
            raise ValueError("从已保存旅行保留地点时需要旅行 ID 和版本")
        return self


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
    discovered_place_ids: list[str] = Field(default_factory=list, max_length=12)
    discovery_note: str = ""


class Activity(Schema):
    id: str
    place_id: str
    day: int
    start: str
    end: str
    locked: bool = False
    reason: str
    time_intent: Literal["any", "daytime", "sunset", "evening", "night"] = "any"


class PlannedStop(Schema):
    place_id: str = Field(min_length=1, max_length=100)
    start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    reason: str = Field(default="", max_length=400)
    time_intent: Literal["any", "daytime", "sunset", "evening", "night"] = "any"
    earliest_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    latest_end: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("活动结束时间必须晚于开始时间")
        return self


class ScheduleBreak(Schema):
    start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    kind: Literal["meal", "rest", "free"] = "rest"
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("用餐休息结束时间必须晚于开始时间")
        return self


class ScheduledDay(Schema):
    day: int = Field(ge=1, le=3)
    activities: list[PlannedStop]
    breaks: list[ScheduleBreak] = Field(default_factory=list)
    weather_note: str = Field(default="", max_length=500)


class RepairScheduledDay(Schema):
    """Read compatibility for old segmented responses; new prompts use ScheduledDay."""

    day: int = Field(ge=1, le=3)
    evening_activities: list[PlannedStop]
    evening_explanation: str = Field(min_length=1, max_length=250)
    daytime_activities: list[PlannedStop]
    breaks: list[ScheduleBreak] = Field(default_factory=list)
    weather_note: str = Field(default="", max_length=500)


class DayPlan(Schema):
    day: int
    date: str
    activities: list[Activity]
    legs: list[RouteLeg]
    breaks: list[ScheduleBreak] = Field(default_factory=list)
    weather_note: str = ""


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


class PlaceRecommendation(Schema):
    place_id: str = Field(max_length=100)
    reason: str = Field(min_length=1, max_length=400)
    role: Literal["landmark", "interest", "neighborhood", "rest", "alternative"] = "interest"
    source_ids: list[str] = Field(default_factory=list, max_length=6)
    tradeoff: str = Field(default="", max_length=250)
    basis: Literal["model", "rule"] = "model"


class VenueAssessment(Schema):
    place_id: str = Field(min_length=1, max_length=100)
    indoor_outdoor: Literal["indoor", "outdoor", "mixed", "unknown"]
    confidence: Literal["high", "medium", "low"] = "medium"
    reason: str = Field(min_length=1, max_length=300)
    basis: Literal["model"] = "model"


class OutputIssue(Schema):
    code: str = Field(max_length=50)
    field: str = Field(max_length=160)
    message: str = Field(max_length=500)


class ReviewFailure(Schema):
    code: str = Field(max_length=40)
    message: str = Field(max_length=250)
    retryable: bool = False
    details: list[OutputIssue] = Field(default_factory=list, max_length=8)


class ReviewIssue(Schema):
    category: Literal["evening", "pace", "weather", "diversity", "routing", "rationale"]
    day: int = Field(ge=1, le=3)
    place_ids: list[str] = Field(default_factory=list, max_length=10)
    alternative_ids: list[str] = Field(default_factory=list, max_length=6)
    evidence: str = Field(min_length=1, max_length=350)
    suggestion: str = Field(min_length=1, max_length=350)


class DayReview(Schema):
    day: int = Field(ge=1, le=3)
    evening: Literal["scheduled", "rest_justified", "not_applicable", "missing"]
    pace: Literal["appropriate", "rest_justified", "too_sparse", "too_dense"]
    explanation: str = Field(min_length=1, max_length=350)


class ScheduleReviewAnalysis(Schema):
    summary: str = Field(min_length=1, max_length=500)
    days: list[DayReview] = Field(min_length=1, max_length=3)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=10)
    overall_score: float | None = Field(default=None, ge=0, le=100)


class ScheduleReview(Schema):
    status: Literal["not_requested", "passed", "changes_requested", "unavailable"] = "not_requested"
    summary: str = Field(default="", max_length=500)
    days: list[DayReview] = Field(default_factory=list, max_length=3)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=10)
    input_digest: str = ""
    failure_code: str = ""
    overall_score: float | None = Field(default=None, ge=0, le=100)


class PlanningDecision(Schema):
    source: Literal["model_schedule", "model_days", "model_order", "rules", "legacy"] = "legacy"
    model_calls: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list, max_length=8)
    recommendations: list[PlaceRecommendation] = Field(default_factory=list, max_length=40)
    review_rounds: int = Field(default=0, ge=0, le=1)
    review_outcome: Literal["not_requested", "applied", "unchanged", "failed", "budget_exhausted"] = (
        "not_requested"
    )
    selected_attempt: int = Field(default=1, ge=1, le=2)
    weather_provided: bool = False
    place_assessments: list[VenueAssessment] = Field(default_factory=list, max_length=40)
    review_failure: ReviewFailure | None = None
    quality_review: ScheduleReview = Field(default_factory=ScheduleReview)


class Itinerary(Schema):
    status: Literal["FEASIBLE", "NEEDS_CONFIRMATION", "CONFLICT", "NO_SOLUTION_WITHIN_LIMIT"]
    days: list[DayPlan]
    checks: list[Check] = Field(default_factory=list)
    weather: ToolResult
    summary: str = ""
    trace: list[dict[str, Any]] = Field(default_factory=list)
    planning_mode: str
    tool_provider: str
    planning_decision: PlanningDecision = Field(default_factory=PlanningDecision)
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


class DeleteTripRequest(Schema):
    base_version: int = Field(ge=1)


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
