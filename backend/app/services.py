import logging
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.agent import generate
from app.catalog import CATALOG, decode_catalog, demo_catalog
from app.database import (
    ActionRecord,
    CatalogRecord,
    ProposalRecord,
    RunRecord,
    TripRecord,
    VersionRecord,
    now,
)
from app.geography import DEFAULT_CITY_ID, get_city, validate_destination
from app.schemas import Itinerary, Location, ProposalRequest, Requirements
from app.tools import ToolUnavailable
from app.validation import validate_itinerary


def require_trip(db, trip_id: str) -> TripRecord:
    trip = db.get(TripRecord, trip_id)
    if trip is None:
        raise HTTPException(404, "旅行不存在")
    return trip


def check_places(req: Requirements, catalog=None):
    catalog = catalog if catalog is not None else CATALOG
    ids = [*req.must_visit, *(a.place_id for a in req.appointments)]
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
    }


def execute_run(factory, settings, run_id: str, request: ProposalRequest | None = None):
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
        overrides = {}
        label = "首次行程" if base is None else "重新生成行程"
        if request:
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
                    overrides["allowed_place_ids"] = [
                        a["place_id"]
                        for d in base["days"]
                        if d["day"] == request.day
                        for a in d["activities"]
                        if a["id"] != target["id"]
                    ]
                result = generate(requirements, settings, emit, overrides, catalog)
        else:
            result = generate(requirements, settings, emit, catalog=catalog)
        emit("completed", "结果已生成，请检查待确认信息和修改预览")
        with factory.begin() as db:
            run = db.get(RunRecord, run_id)
            proposal = ProposalRecord(
                trip_id=run.trip_id,
                base_version=run.base_version,
                itinerary=result.model_dump(mode="json"),
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
    validated = validate_itinerary(
        Requirements(**trip.requirements),
        Itinerary(**itinerary),
        catalog=decode_catalog(trip.catalog, trip.requirements.get("city_id")),
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
        .values(current_version=base_version + 1)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "行程被其他操作更新，请刷新")
    number = base_version + 1
    db.add(VersionRecord(trip_id=trip_id, number=number, itinerary=itinerary, label=label))
    db.add(ActionRecord(trip_id=trip_id, key=key, resource=resource, version=number))
    if proposal:
        proposal.applied_version = number
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "操作与其他提交冲突，请使用相同幂等键重试") from exc
    return {"version": number, "replayed": False}
