import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any

from alembic import command
from alembic.config import Config
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import inspect, select, update

from app.agent_tasks import task_views
from app.catalog import decode_catalog, demo_catalog
from app.config import ROOT, Settings, get_settings
from app.database import (
    AgentTaskRecord,
    CatalogRecord,
    ProposalRecord,
    RunRecord,
    TripRecord,
    VersionRecord,
    build_database,
    now,
)
from app.geography import DEFAULT_CITY_ID, get_city, grouped_cities, regions
from app.local_config import clear_config, config_view, require_local_request, write_config
from app.schemas import (
    ApplyRequest,
    CityQuery,
    ConnectionTest,
    HotelChoiceRequest,
    Itinerary,
    Location,
    LocationSearchRequest,
    NaturalRevisionRequest,
    ProposalRequest,
    Requirements,
    RestoreRequest,
)
from app.services import (
    apply_snapshot,
    current_itinerary,
    execute_run,
    prepare_trip,
    require_trip,
    trip_view,
)
from app.tools import ToolUnavailable, TravelTools
from app.user_config import request_settings, test_model_connection


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine, factory = build_database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Upgrade the local schema as features grow. PostgreSQL uses the explicit CLI migration.
        if settings.database_url.startswith("sqlite"):
            config = Config(str(ROOT / "backend/alembic.ini"))
            config.attributes["database_url"] = settings.database_url
            tables = set(inspect(engine).get_table_names())
            if "trips" in tables and "alembic_version" not in tables:
                columns = {c["name"] for c in inspect(engine).get_columns("trips")}
                if columns != {"id", "requirements", "current_version", "created_at"}:
                    raise RuntimeError("无法识别本地旧数据库版本，请先备份并检查迁移状态")
                command.stamp(config, "0001")
            command.upgrade(config, "head")
        with factory.begin() as db:
            db.execute(
                update(AgentTaskRecord)
                .where(AgentTaskRecord.status == "running")
                .values(status="interrupted", error="服务重启中断了此子任务。", finished_at=now())
            )
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
    def health(request: Request):
        runtime = request_settings(settings, request)
        return {
            "status": "ok",
            "tool_provider": runtime.tool_provider,
            "planning_mode": runtime.planning_mode,
            "catalog_mode": "demo",
            "llm_configured": bool(runtime.llm_base_url and runtime.llm_api_key and runtime.llm_model),
            "credential_policy": "project_local_file_or_user_supplied",
        }

    @app.get("/api/settings/local")
    def get_local_config(request: Request, response: Response):
        require_local_request(request)
        response.headers["Cache-Control"] = "no-store"
        return config_view(settings)

    @app.post("/api/settings/local")
    def save_local_config(request: Request, response: Response, body: Annotated[Any, Body()]):
        require_local_request(request)
        response.headers["Cache-Control"] = "no-store"
        return write_config(settings, body)

    @app.post("/api/settings/local/clear")
    def clear_local_config(request: Request, response: Response):
        require_local_request(request)
        response.headers["Cache-Control"] = "no-store"
        return clear_config(settings)

    @app.get("/api/geography/countries")
    def countries():
        return sorted(regions()["countries"], key=lambda c: (c["code"] != "CN", c["name"]))

    @app.get("/api/geography/states")
    def states(country_code: str = Query(pattern="^[A-Z]{2}$")):
        return [
            {**state, "city_count": len(grouped_cities().get(state["id"], []))}
            for state in regions()["states"]
            if state["country_code"] == country_code and grouped_cities().get(state["id"])
        ]

    @app.get("/api/geography/cities")
    def cities(
        state_id: str = Query(max_length=30),
        q: str = Query(default="", max_length=100),
        limit: int = Query(default=500, ge=1, le=5000),
    ):
        matches = [
            c
            for c in grouped_cities().get(state_id, [])
            if q.casefold() in c["name"].casefold() or q.casefold() in c["english_name"].casefold()
        ]
        return {"items": matches[:limit], "total": len(matches)}

    @app.get("/api/geography/cities/{city_id}")
    def city(city_id: int):
        try:
            return get_city(city_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/places")
    def places(q: str = Query(default="", max_length=100), city_id: int = DEFAULT_CITY_ID):
        try:
            catalog = demo_catalog(city_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return [p.model_dump() for p in catalog.values() if q in p.name or q in p.description]

    @app.post("/api/places/discover")
    def discover(body: CityQuery, request: Request):
        runtime = request_settings(settings, request)
        try:
            destination = get_city(body.city_id)
            tools = TravelTools(runtime, destination["name"], destination["country_code"])
            result = tools.discover(destination)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ToolUnavailable as exc:
            raise HTTPException(400, str(exc)) from exc
        with factory.begin() as db:
            snapshot = CatalogRecord(city_id=body.city_id, places=result["places"])
            db.add(snapshot)
            db.flush()
            return {
                "catalog_id": snapshot.id,
                "places": list(result["places"].values()),
                "center": result["center"],
            }

    @app.post("/api/settings/test")
    def test_connection(body: ConnectionTest, request: Request):
        runtime = request_settings(settings, request)
        if body.service == "model":
            return test_model_connection(runtime)
        if runtime.tool_provider != "amap":
            raise HTTPException(400, "请先选择使用自己的高德 Key")
        try:
            TravelTools(runtime)._get("/v3/config/district", {"keywords": "中国", "subdistrict": 0})
        except ToolUnavailable as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "message": "高德 Web 服务已响应；其他接口权限会在使用时进一步检查。"}

    @app.post("/api/trips", status_code=201)
    def create_trip(requirements: Requirements):
        with factory.begin() as db:
            requirements, catalog = prepare_trip(db, requirements)
            trip = TripRecord(
                requirements=requirements.model_dump(mode="json"),
                catalog={key: p.model_dump() for key, p in catalog.items()},
            )
            db.add(trip)
            db.flush()
            return trip_view(db, trip)

    @app.post("/api/locations/search")
    def search_locations(body: LocationSearchRequest, request: Request):
        runtime = request_settings(settings, request)
        if runtime.tool_provider == "demo":
            if body.center is None:
                raise HTTPException(422, "演示模式需要城市参考位置，或手动填写抵离坐标。")
            return {
                "locations": [
                    body.center.model_copy(update={"name": body.query + "（演示位置）"}).model_dump()
                ],
                "note": "演示坐标，不代表真实车站或机场；真实查询请启用高德。",
            }
        try:
            data = TravelTools(runtime, city=body.city)._get(
                "/v3/place/text",
                {
                    "keywords": body.query,
                    "city": body.city,
                    "citylimit": "true",
                    "offset": 5,
                },
            )
            locations = []
            for poi in data.get("pois", [])[:5]:
                try:
                    longitude, latitude = map(float, poi["location"].split(","))
                    locations.append(
                        Location(name=poi["name"][:100], longitude=longitude, latitude=latitude).model_dump()
                    )
                except (ValueError, KeyError, TypeError):
                    continue
            return {"locations": locations, "note": "请选择准确的车站、机场或地址，同名地点请核对坐标。"}
        except ToolUnavailable as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/trips/{trip_id}")
    def get_trip(trip_id: str):
        with factory() as db:
            return trip_view(db, require_trip(db, trip_id))

    @app.post("/api/trips/{trip_id}/plan-runs", status_code=202)
    def plan_trip(trip_id: str, background: BackgroundTasks, request: Request):
        runtime = request_settings(settings, request)
        with factory.begin() as db:
            trip = require_trip(db, trip_id)
            run = RunRecord(trip_id=trip_id, base_version=trip.current_version)
            db.add(run)
            db.flush()
            run_id = run.id
        background.add_task(execute_run, factory, runtime, run_id)
        return {"run_id": run_id}

    @app.post("/api/trips/{trip_id}/proposals", status_code=202)
    def propose(trip_id: str, body: ProposalRequest, background: BackgroundTasks, request: Request):
        runtime = request_settings(settings, request)
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
        background.add_task(execute_run, factory, runtime, run_id, body)
        return {"run_id": run_id}

    @app.post("/api/trips/{trip_id}/hotel-selections", status_code=202)
    def choose_hotel(trip_id: str, body: HotelChoiceRequest, background: BackgroundTasks, request: Request):
        runtime = request_settings(settings, request)
        with factory.begin() as db:
            trip = require_trip(db, trip_id)
            if trip.current_version != body.base_version:
                raise HTTPException(409, "行程版本已变化，请刷新后选择酒店。")
            base = current_itinerary(db, trip)
            if base is None or trip.requirements.get("lodging_mode") != "route_first":
                raise HTTPException(409, "请先保存一份路线优先的行程草案。")
            stay = next((n for n in Itinerary(**base).lodging.nights if n.night == body.night), None)
            if stay is None or not stay.last_place_id or not stay.next_place_id:
                raise HTTPException(422, "这一晚没有完整的住宿搜索条件。")
            if body.candidate_id and not any(h.id == body.candidate_id for h in stay.candidates):
                raise HTTPException(422, "所选酒店不属于这一晚的候选列表。")
            run = RunRecord(trip_id=trip_id, base_version=body.base_version)
            db.add(run)
            db.flush()
            run_id = run.id
        background.add_task(execute_run, factory, runtime, run_id, body)
        return {"run_id": run_id}

    @app.post("/api/trips/{trip_id}/revisions", status_code=202)
    def revise(trip_id: str, body: NaturalRevisionRequest, background: BackgroundTasks, request: Request):
        runtime = request_settings(settings, request)
        with factory.begin() as db:
            trip = require_trip(db, trip_id)
            if trip.current_version != body.base_version:
                raise HTTPException(409, "行程版本已变化，请刷新后重新描述修改。")
            if current_itinerary(db, trip) is None:
                raise HTTPException(409, "请先保存路线草案。")
            run = RunRecord(trip_id=trip_id, base_version=body.base_version)
            db.add(run)
            db.flush()
            run_id = run.id
        background.add_task(execute_run, factory, runtime, run_id, body)
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
                "agent_tasks": [
                    {key: value for key, value in task.items() if key not in {"input", "result"}}
                    for task in task_views(db, run_id)
                ],
                "proposal": {
                    "id": proposal.id,
                    "base_version": proposal.base_version,
                    "label": proposal.label,
                    "itinerary": proposal.itinerary,
                    "requirements": proposal.requirements,
                    "places": [
                        p.model_dump()
                        for p in decode_catalog(
                            proposal.catalog
                            if proposal.catalog is not None
                            else require_trip(db, run.trip_id).catalog
                        ).values()
                    ],
                    "applied_version": proposal.applied_version,
                    "dismissed": proposal.dismissed,
                }
                if proposal
                else None,
            }

    @app.get("/api/runs/{run_id}/agent-tasks")
    def get_agent_tasks(run_id: str):
        with factory() as db:
            if db.get(RunRecord, run_id) is None:
                raise HTTPException(404, "任务不存在")
            return task_views(db, run_id)

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
                requirements_snapshot=version.requirements,
                catalog_snapshot=version.catalog,
            )

    return app


app = create_app()
