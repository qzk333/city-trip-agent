import logging
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.agent import generate
from app.agent_tasks import TaskJournal
from app.catalog import CATALOG, decode_catalog, demo_catalog
from app.database import (
    ActionRecord,
    AgentTaskRecord,
    CatalogRecord,
    ProposalRecord,
    RunRecord,
    TripRecord,
    VersionRecord,
    now,
)
from app.geography import DEFAULT_CITY_ID, get_city, validate_destination
from app.hotel_routes import select_hotel
from app.replanning import run_revision
from app.revisions import interpret_revision, review_hotel_choice
from app.schemas import (
    HotelChoiceRequest,
    Itinerary,
    Location,
    NaturalRevisionRequest,
    ProposalRequest,
    Requirements,
)
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def require_trip(db, trip_id: str) -> TripRecord:
    trip = db.get(TripRecord, trip_id)
    if trip is None:
        raise HTTPException(404, "旅行不存在")
    return trip


def check_places(req: Requirements, catalog=None):
    catalog = catalog if catalog is not None else CATALOG
    ids = [*req.must_visit, *req.place_days, *(a.place_id for a in req.appointments)]
    if any(place_id not in catalog for place_id in ids):
        raise HTTPException(422, "存在未确认的地点，请从地点目录选择")


def prepare_trip(db, req: Requirements):
    try:
        normalized = validate_destination(req)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    city = get_city(normalized.city_id)
    if "hotel" not in req.model_fields_set and city["id"] != DEFAULT_CITY_ID:
        normalized = normalized.model_copy(
            update={
                "hotel": Location(
                    name=f"参考起点 · {city['name']}",
                    longitude=city["longitude"],
                    latitude=city["latitude"],
                    coordinate_system="WGS84",
                )
            }
        )
    if normalized.catalog_id:
        snapshot = db.get(CatalogRecord, normalized.catalog_id)
        if snapshot is None or snapshot.city_id != normalized.city_id:
            raise HTTPException(422, "地点目录与目的地不匹配，请重新加载城市地点")
        catalog = decode_catalog(snapshot.places, normalized.city_id)
    else:
        catalog = demo_catalog(normalized.city_id)
    check_places(normalized, catalog)
    return normalized, catalog


def current_itinerary(db, trip: TripRecord) -> dict | None:
    version = db.scalar(
        select(VersionRecord).where(
            VersionRecord.trip_id == trip.id,
            VersionRecord.number == trip.current_version,
        )
    )
    return version.itinerary if version else None


def trip_view(db, trip: TripRecord) -> dict:
    latest_run = db.scalar(
        select(RunRecord).where(RunRecord.trip_id == trip.id).order_by(RunRecord.created_at.desc()).limit(1)
    )
    return {
        "id": trip.id,
        "requirements": trip.requirements,
        "current_version": trip.current_version,
        "itinerary": current_itinerary(db, trip),
        "latest_run_id": latest_run.id if latest_run else None,
        "places": [
            p.model_dump() for p in decode_catalog(trip.catalog, trip.requirements.get("city_id")).values()
        ],
    }


def make_overrides(base: dict, request: ProposalRequest) -> dict:
    day = next((day for day in base["days"] if day["day"] == request.day), None)
    if day is None:
        raise HTTPException(422, "调整日期超出行程")
    delays = dict(base.get("day_start_offsets", {}))
    if request.action == "delay":
        key = str(request.day)
        delays[key] = delays.get(key, 0) + request.delay_minutes
    return {
        "fixed": [a for a in day["activities"] if a["locked"]],
        "preferred": [a["place_id"] for a in day["activities"]],
        "frozen_days": [d for d in base["days"] if d["day"] != request.day],
        "delays": delays,
        "indoor": request.action == "indoor",
        "lodging": base.get("lodging", {}),
    }


def legacy_place_days(db, trip_id, number, base):
    """Recover only confirmed, still-applicable explicit moves from older versions."""
    pins = {}
    restored_at = (
        db.scalar(
            select(VersionRecord.number)
            .where(
                VersionRecord.trip_id == trip_id,
                VersionRecord.number <= number,
                VersionRecord.label.like("从版本 % 恢复"),
            )
            .order_by(VersionRecord.number.desc())
            .limit(1)
        )
        or 0
    )
    tasks = db.scalars(
        select(AgentTaskRecord)
        .join(RunRecord, AgentTaskRecord.run_id == RunRecord.id)
        .join(ProposalRecord, RunRecord.proposal_id == ProposalRecord.id)
        .where(
            RunRecord.trip_id == trip_id,
            AgentTaskRecord.role == "main",
            ProposalRecord.applied_version.is_not(None),
            ProposalRecord.applied_version <= number,
            ProposalRecord.applied_version > restored_at,
        )
        .order_by(ProposalRecord.applied_version)
    )
    for task in tasks:
        for operation in task.result.get("intent", {}).get("operations", []):
            pid = operation.get("place_id")
            if not pid:
                continue
            if operation.get("kind") in {"add", "move"}:
                pins[pid] = (
                    operation.get("target_day") if operation["kind"] == "move" else operation.get("day")
                )
            elif operation.get("kind") == "remove":
                pins.pop(pid, None)
    actual = {a["place_id"]: day["day"] for day in base["days"] for a in day["activities"]}
    return {pid: day for pid, day in pins.items() if actual.get(pid) == day}


def execute_run(
    factory,
    settings,
    run_id: str,
    request: ProposalRequest | HotelChoiceRequest | NaturalRevisionRequest | None = None,
):
    journal = TaskJournal(factory, run_id)

    def emit(stage: str, message: str):
        with factory.begin() as db:
            run = db.get(RunRecord, run_id)
            run.events = [
                *run.events,
                {"id": len(run.events) + 1, "stage": stage, "message": message, "at": now()},
            ]

    try:
        with factory.begin() as db:
            run = db.get(RunRecord, run_id)
            run.status = "running"
            trip = require_trip(db, run.trip_id)
            requirements = Requirements(**trip.requirements)
            catalog = decode_catalog(trip.catalog, requirements.city_id)
            # A concurrent confirmation must not change the baseline of this run.
            version = db.scalar(
                select(VersionRecord).where(
                    VersionRecord.trip_id == trip.id,
                    VersionRecord.number == run.base_version,
                )
            )
            base = deepcopy(version.itinerary) if version else None
            if version and version.requirements:
                requirements = Requirements(**version.requirements)
            if version and version.catalog is not None:
                catalog = decode_catalog(version.catalog, requirements.city_id)
            if base and version and "place_days" not in (version.requirements or trip.requirements):
                requirements.place_days = legacy_place_days(db, trip.id, run.base_version, base)
        overrides = {}
        label = "首次行程" if base is None else "重新生成行程"
        if isinstance(request, NaturalRevisionRequest):
            if base is None:
                raise ToolUnavailable("请先保存路线，再描述修改要求。")
            tools = TravelTools(
                settings, requirements.city, requirements.country_code, province=requirements.province
            )
            label = "自然语言行程调整"
            emit("understanding", "主 Agent 正在理解修改要求，保留已选住宿和锁定活动")
            with journal.task(
                "main", {"action": "natural_revision", **request.model_dump(exclude={"base_version"})}
            ) as task:
                intent = interpret_revision(request.message, requirements, Itinerary(**base), catalog, tools)
                if request.scope is not None:
                    intent.scope = request.scope
                if request.reselect:
                    intent.reselect, intent.allow_drop_optional = True, True
                emit("planning", intent.summary)
                requirements, result, catalog, audit = run_revision(
                    requirements,
                    Itinerary(**base),
                    intent,
                    tools,
                    catalog,
                    message=request.message,
                    emit=emit,
                )
                task.complete(
                    {
                        "intent": intent.model_dump(mode="json"),
                        "itinerary_status": result.status,
                        "requirements": requirements.model_dump(mode="json"),
                        "replanning": audit,
                    },
                    status="degraded"
                    if result.revision_status == "unavailable"
                    or any(c.severity == "error" for c in result.checks)
                    else "completed",
                )
        elif isinstance(request, HotelChoiceRequest):
            if base is None:
                raise ToolUnavailable("请先保存路线草案，再选择住宿。")
            label = f"第 {request.night} 晚住宿调整"
            emit("planning", "按你的酒店选择补齐相邻两天交通，保留景点与锁定活动")
            tools = TravelTools(
                settings, requirements.city, requirements.country_code, province=requirements.province
            )
            with journal.task("main", {"action": "select_hotel", **request.model_dump(mode="json")}) as task:
                result = select_hotel(requirements, Itinerary(**base), request, tools, catalog)
                emit("reviewing", "主 Agent 复核住宿变化是否需要重排行程，再进行独立检查")
                result = review_hotel_choice(requirements, result, request.night, tools, catalog)
                task.complete(
                    {
                        "night": request.night,
                        "lodging": result.lodging.model_dump(mode="json"),
                        "itinerary_status": result.status,
                        "review_status": result.revision_status,
                        "review_note": result.revision_note,
                    }
                )
        elif request:
            if base is None:
                raise ToolUnavailable("请先确认一份行程，再进行调整")
            label = {
                "delay": f"第 {request.day} 天推迟 {request.delay_minutes} 分钟",
                "lock": "锁定活动",
                "unlock": "解除活动锁定",
                "remove": "移除活动",
                "indoor": f"第 {request.day} 天优先室内活动",
            }[request.action]
            overrides = make_overrides(base, request)
            if request.action in ("lock", "unlock"):
                result = Itinerary(**base)
                target = next(a for d in result.days for a in d.activities if a.id == request.activity_id)
                target.locked = request.action == "lock"
                emit("validating", "保留现有行程，只更新活动锁定状态")
            else:
                if request.action == "remove":
                    target = next(
                        a for d in base["days"] for a in d["activities"] if a["id"] == request.activity_id
                    )
                    overrides["excluded"] = [target["place_id"]]
                    requirements.place_days.pop(target["place_id"], None)
                    overrides["allowed_place_ids"] = [
                        a["place_id"]
                        for d in base["days"]
                        if d["day"] == request.day
                        for a in d["activities"]
                        if a["id"] != target["id"]
                    ]
                result = generate(requirements, settings, emit, overrides, catalog, journal=journal)
        else:
            result = generate(requirements, settings, emit, catalog=catalog, journal=journal)
        emit("completed", "结果已生成，请检查待确认信息和修改预览")
        with factory.begin() as db:
            run = db.get(RunRecord, run_id)
            proposal = ProposalRecord(
                trip_id=run.trip_id,
                base_version=run.base_version,
                itinerary=result.model_dump(mode="json"),
                requirements=requirements.model_dump(mode="json"),
                catalog={pid: place.model_dump() for pid, place in catalog.items()},
                label=label,
            )
            db.add(proposal)
            db.flush()
            run.proposal_id, run.status = proposal.id, "completed"
    except Exception as exc:
        logging.getLogger(__name__).error("Planning failed: %s", type(exc).__name__)
        with factory.begin() as db:
            run = db.get(RunRecord, run_id)
            run.status = "failed"
            run.error = str(exc) if isinstance(exc, ToolUnavailable) else "行程生成失败，请重试或检查本地配置"
            run.events = [
                *run.events,
                {"id": len(run.events) + 1, "stage": "failed", "message": run.error, "at": now()},
            ]


def apply_snapshot(
    db,
    trip_id: str,
    base_version: int,
    key: str,
    resource: str,
    itinerary: dict,
    label: str,
    proposal: ProposalRecord | None = None,
    requirements_snapshot: dict | None = None,
    catalog_snapshot: dict | None = None,
) -> dict:
    trip = require_trip(db, trip_id)
    previous = db.scalar(select(ActionRecord).where(ActionRecord.trip_id == trip_id, ActionRecord.key == key))
    if previous:
        if previous.resource != resource:
            raise HTTPException(409, "该幂等键已用于其他操作")
        return {"version": previous.version, "replayed": True}
    if proposal and proposal.applied_version is not None:
        raise HTTPException(409, "该方案已确认，请刷新当前行程")
    if proposal and proposal.dismissed:
        raise HTTPException(409, "该调整已取消，请重新生成方案")
    if trip.current_version != base_version:
        raise HTTPException(409, "行程版本已经变化，请加载最新版本后重新操作")
    snapshot = requirements_snapshot or (proposal.requirements if proposal else None) or trip.requirements
    places = (
        catalog_snapshot
        if catalog_snapshot is not None
        else proposal.catalog
        if proposal and proposal.catalog is not None
        else trip.catalog
    )
    validated = validate_itinerary(
        Requirements(**snapshot),
        Itinerary(**itinerary),
        catalog=decode_catalog(places, snapshot.get("city_id")),
    )
    if any(c.severity == "error" for c in validated.checks):
        raise HTTPException(422, "方案仍有未解决的约束或路线问题，不能确认为可执行行程")
    if validated.status in ("CONFLICT", "NO_SOLUTION_WITHIN_LIMIT"):
        raise HTTPException(422, "请先解决行程冲突")
    changed = db.execute(
        update(TripRecord)
        .where(
            TripRecord.id == trip_id,
            TripRecord.current_version == base_version,
        )
        .values(current_version=base_version + 1, requirements=snapshot, catalog=places)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "行程被其他操作更新，请刷新")
    number = base_version + 1
    db.add(
        VersionRecord(
            trip_id=trip_id,
            number=number,
            itinerary=itinerary,
            label=label,
            requirements=snapshot,
            catalog=places,
        )
    )
    db.add(ActionRecord(trip_id=trip_id, key=key, resource=resource, version=number))
    if proposal:
        proposal.applied_version = number
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "操作与其他提交冲突，请使用相同幂等键重试") from exc
    return {"version": number, "replayed": False}
