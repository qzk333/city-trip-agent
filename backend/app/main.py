import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update

from app.catalog import CATALOG
from app.config import Settings, get_settings
from app.database import Base, ProposalRecord, RunRecord, TripRecord, VersionRecord, build_database
from app.schemas import ApplyRequest, ProposalRequest, Requirements, RestoreRequest
from app.services import (
    apply_snapshot,
    check_places,
    current_itinerary,
    execute_run,
    require_trip,
    trip_view,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine, factory = build_database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # M1 local bootstrap. PostgreSQL deployments should use Alembic migrations.
        if settings.database_url.startswith("sqlite"):
            Base.metadata.create_all(engine)
        with factory.begin() as db:
            db.execute(
                update(RunRecord)
                .where(RunRecord.status.in_(["queued", "running"]))
                .values(status="failed", error="本地服务重启中断了任务，请重新生成；旧确认行程仍保留")
            )
        yield
        engine.dispose()

    app = FastAPI(title="城市旅行 Agent", version="0.1.0.dev0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    app.state.session_factory = factory

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "tool_provider": settings.tool_provider,
            "planning_mode": settings.planning_mode,
            "catalog_mode": "demo",
            "llm_configured": bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model),
        }

    @app.get("/api/places")
    def places(q: str = Query(default="", max_length=100)):
        return [p.model_dump() for p in CATALOG.values() if q in p.name or q in p.description]

    @app.post("/api/trips", status_code=201)
    def create_trip(requirements: Requirements):
        check_places(requirements)
        with factory.begin() as db:
            trip = TripRecord(requirements=requirements.model_dump(mode="json"))
            db.add(trip)
            db.flush()
            return trip_view(db, trip)

    @app.get("/api/trips/{trip_id}")
    def get_trip(trip_id: str):
        with factory() as db:
            return trip_view(db, require_trip(db, trip_id))

    @app.post("/api/trips/{trip_id}/plan-runs", status_code=202)
    def plan_trip(trip_id: str, background: BackgroundTasks):
        with factory.begin() as db:
            trip = require_trip(db, trip_id)
            run = RunRecord(trip_id=trip_id, base_version=trip.current_version)
            db.add(run)
            db.flush()
            run_id = run.id
        background.add_task(execute_run, factory, settings, run_id)
        return {"run_id": run_id}

    @app.post("/api/trips/{trip_id}/proposals", status_code=202)
    def propose(trip_id: str, body: ProposalRequest, background: BackgroundTasks):
        with factory.begin() as db:
            trip = require_trip(db, trip_id)
            if trip.current_version != body.base_version:
                raise HTTPException(409, "行程版本已变化，请刷新")
            base = current_itinerary(db, trip)
            if base is None:
                raise HTTPException(409, "请先确认初始行程")
            if body.day > trip.requirements["days"]:
                raise HTTPException(422, "调整日期超出旅行天数")
            if body.action in ("lock", "unlock", "remove"):
                target = next(
                    (a for d in base["days"] for a in d["activities"] if a["id"] == body.activity_id), None
                )
                if target is None or target["day"] != body.day:
                    raise HTTPException(422, "活动不存在或日期不匹配")
                if body.action == "unlock" and "-fixed" in target["id"]:
                    raise HTTPException(409, "固定预约不能解除锁定")
                if body.action == "remove" and (
                    target["locked"] or target["place_id"] in trip.requirements["must_visit"]
                ):
                    raise HTTPException(409, "锁定活动或必去地点不能直接移除")
            run = RunRecord(trip_id=trip_id, base_version=trip.current_version)
            db.add(run)
            db.flush()
            run_id = run.id
        background.add_task(execute_run, factory, settings, run_id, body)
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        with factory() as db:
            run = db.get(RunRecord, run_id)
            if not run:
                raise HTTPException(404, "任务不存在")
            proposal = db.get(ProposalRecord, run.proposal_id) if run.proposal_id else None
            return {
                "id": run.id,
                "status": run.status,
                "events": run.events,
                "error": run.error,
                "proposal": {
                    "id": proposal.id,
                    "base_version": proposal.base_version,
                    "label": proposal.label,
                    "itinerary": proposal.itinerary,
                    "applied_version": proposal.applied_version,
                    "dismissed": proposal.dismissed,
                }
                if proposal
                else None,
            }

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, after: int = Query(default=0, ge=0)):
        get_run(run_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError as exc:
            raise HTTPException(422, "事件游标格式错误") from exc

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                payload = await asyncio.to_thread(get_run, run_id)
                for event in payload["events"]:
                    if event["id"] > cursor:
                        cursor = event["id"]
                        yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if payload["status"] in ("completed", "failed"):
                    yield f"event: done\ndata: {json.dumps({'status': payload['status']})}\n\n"
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/trips/{trip_id}/proposals/{proposal_id}/apply")
    def apply(trip_id: str, proposal_id: str, body: ApplyRequest):
        with factory() as db:
            proposal = db.get(ProposalRecord, proposal_id)
            if not proposal or proposal.trip_id != trip_id:
                raise HTTPException(404, "调整方案不存在")
            if body.base_version != proposal.base_version:
                raise HTTPException(409, "提交基准与方案基准不一致")
            return apply_snapshot(
                db,
                trip_id,
                body.base_version,
                body.idempotency_key,
                f"proposal/{proposal_id}",
                proposal.itinerary,
                proposal.label,
                proposal,
            )

    @app.post("/api/trips/{trip_id}/proposals/{proposal_id}/dismiss")
    def dismiss(trip_id: str, proposal_id: str):
        with factory.begin() as db:
            proposal = db.get(ProposalRecord, proposal_id)
            if not proposal or proposal.trip_id != trip_id:
                raise HTTPException(404, "调整方案不存在")
            if proposal.applied_version is not None:
                raise HTTPException(409, "已经确认的方案不能取消")
            proposal.dismissed = True
        return {"dismissed": True}

    @app.get("/api/trips/{trip_id}/versions")
    def versions(trip_id: str):
        with factory() as db:
            require_trip(db, trip_id)
            return [
                {"number": v.number, "label": v.label, "created_at": v.created_at}
                for v in db.scalars(
                    select(VersionRecord)
                    .where(VersionRecord.trip_id == trip_id)
                    .order_by(VersionRecord.number.desc())
                )
            ]

    @app.post("/api/trips/{trip_id}/versions/{number}/restore")
    def restore(trip_id: str, number: int, body: RestoreRequest):
        with factory() as db:
            version = db.scalar(
                select(VersionRecord).where(
                    VersionRecord.trip_id == trip_id,
                    VersionRecord.number == number,
                )
            )
            if version is None:
                raise HTTPException(404, "历史版本不存在")
            return apply_snapshot(
                db,
                trip_id,
                body.base_version,
                body.idempotency_key,
                f"restore/{number}",
                version.itinerary,
                f"从版本 {number} 恢复",
            )

    return app


app = create_app()
