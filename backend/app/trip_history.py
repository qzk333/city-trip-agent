"""Confirmed trip summaries and atomic, explicitly requested deletion."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from app.database import ActionRecord, AgentTaskRecord, ProposalRecord, RunRecord, TripRecord, VersionRecord
from app.schemas import Requirements
from app.services import require_trip


def lock_trip(db, trip_id):
    # Serialize run creation and deletion, including SQLite where FOR UPDATE is ignored.
    changed = db.execute(
        update(TripRecord).where(TripRecord.id == trip_id).values(current_version=TripRecord.current_version)
    )
    if changed.rowcount != 1:
        raise HTTPException(404, "旅行不存在")
    return require_trip(db, trip_id)


def confirmed_history(db, q, offset, limit):
    query = (
        select(TripRecord, VersionRecord)
        .join(
            VersionRecord,
            (VersionRecord.trip_id == TripRecord.id) & (VersionRecord.number == TripRecord.current_version),
        )
        .where(TripRecord.current_version > 0)
    )
    if q:
        query = query.where(TripRecord.requirements["city"].as_string().contains(q, autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(VersionRecord.created_at.desc(), TripRecord.id).offset(offset).limit(limit)
    )
    items = []
    for trip, version in rows:
        req = Requirements(**(version.requirements or trip.requirements))
        items.append(
            {
                "id": trip.id,
                "city": req.city,
                "start_date": req.start_date.isoformat(),
                "end_date": (req.start_date + timedelta(days=req.days - 1)).isoformat(),
                "days": req.days,
                "current_version": trip.current_version,
                "confirmed_at": version.created_at,
                "activity_count": sum(
                    len(day.get("activities", [])) for day in version.itinerary.get("days", [])
                ),
            }
        )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def delete_confirmed_trip(db, trip_id, base_version):
    trip = lock_trip(db, trip_id)
    if trip.current_version < 1:
        raise HTTPException(409, "未确认的草案不属于历史行程。")
    if trip.current_version != base_version:
        raise HTTPException(409, "行程版本已变化，请刷新历史列表后重新确认删除。")
    if db.scalar(
        select(RunRecord.id).where(RunRecord.trip_id == trip_id, RunRecord.status.in_(["queued", "running"]))
    ):
        raise HTTPException(409, "该行程还有正在执行的任务，请等待完成后再删除。")
    run_ids = select(RunRecord.id).where(RunRecord.trip_id == trip_id)
    db.execute(delete(AgentTaskRecord).where(AgentTaskRecord.run_id.in_(run_ids)))
    for model in (ProposalRecord, ActionRecord, VersionRecord, RunRecord):
        db.execute(delete(model).where(model.trip_id == trip_id))
    db.execute(delete(TripRecord).where(TripRecord.id == trip_id))
    return {"deleted": True, "trip_id": trip_id}
