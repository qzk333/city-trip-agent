import pytest
from sqlalchemy import func, select

from app.candidate_discovery import query_plan
from app.candidate_ranking import rank_candidates
from app.catalog import CATALOG
from app.database import (
    ActionRecord,
    AgentTaskRecord,
    CatalogRecord,
    ProposalRecord,
    RunRecord,
    TripRecord,
    VersionRecord,
)
from app.schemas import CandidateRequest, Requirements, TravelProfile
from app.travel_profile import discovery_fingerprint


def confirmed(client, **patch):
    trip = client.post("/api/trips", json={"start_date": "2026-10-26", **patch}).json()
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    proposal = client.get(f"/api/runs/{run_id}").json()["proposal"]
    response = client.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/apply",
        json={"base_version": 0, "idempotency_key": "initial-save"},
    )
    assert response.status_code == 200, response.text
    return trip["id"]


@pytest.mark.parametrize("schema", [Requirements, CandidateRequest])
def test_only_themes_survive_legacy_input_and_explicit_themes_win(schema):
    legacy = {"start_date": "2026-10-26", "interests": ["nature", "culture"]}
    if schema is CandidateRequest:
        legacy["city_id"] = 19598
    parsed = schema(**legacy)
    assert parsed.profile.themes == ["nature", "culture"]
    assert "interests" not in parsed.model_dump()
    parsed = schema(**{**legacy, "profile": {"themes": ["night", "night"]}})
    assert parsed.profile.themes == ["night"]
    assert "interests" not in schema.model_json_schema()["properties"]


def test_theme_order_does_not_change_fingerprint_queries_or_scores():
    a = TravelProfile(themes=["culture", "history", "night", "city", "coffee"])
    b = TravelProfile(themes=list(reversed(a.themes)))
    assert query_plan(a) == query_plan(b)
    assert ("博物馆", "culture") not in query_plan(a)
    assert discovery_fingerprint(19598, "2026-10-26", 2, "relaxed", a, "") == discovery_fingerprint(
        19598, "2026-10-26", 2, "relaxed", b, ""
    )
    assert list(rank_candidates(CATALOG, a)) == list(rank_candidates(CATALOG, b))
    fine = rank_candidates(CATALOG, TravelProfile(themes=["history"]))
    both = rank_candidates(CATALOG, TravelProfile(themes=["culture", "history"]))
    assert fine["gushan_museum"].candidate_match.relevance == both["gushan_museum"].candidate_match.relevance


def test_history_only_confirmed_search_and_pending_preview_do_not_replace_saved(client):
    client.post("/api/trips", json={"start_date": "2026-10-26"})
    assert client.get("/api/trips").json()["total"] == 0
    trip_id = confirmed(client)
    client.post(f"/api/trips/{trip_id}/revisions", json={"base_version": 1, "message": "第1天晚出发10分钟"})
    page = client.get("/api/trips?q=杭州&limit=1").json()
    assert page["total"] == 1 and page["items"][0]["id"] == trip_id
    assert page["items"][0]["current_version"] == 1
    assert page["items"][0]["activity_count"] > 0
    assert client.get("/api/trips?q=长沙").json()["items"] == []
    assert client.get("/api/trips?offset=1").json()["items"] == []
    assert client.get("/api/trips?q=%").json()["total"] == 0
    assert client.get("/api/trips?limit=51").status_code == 422


def test_delete_removes_all_trip_children_preserves_other_trip_and_catalog(client):
    trip_id, other = confirmed(client), confirmed(client)
    factory = client.app.state.session_factory
    with factory.begin() as db:
        db.add(CatalogRecord(id="shared", city_id=19598, places={"evidence": "keep"}))
    with factory() as db:
        run_ids = list(db.scalars(select(RunRecord.id).where(RunRecord.trip_id == trip_id)))
        assert (
            db.scalar(
                select(func.count()).select_from(AgentTaskRecord).where(AgentTaskRecord.run_id.in_(run_ids))
            )
            > 0
        )
    response = client.request("DELETE", f"/api/trips/{trip_id}", json={"base_version": 1})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/trips/{trip_id}").status_code == 404
    assert client.get(f"/api/trips/{other}").status_code == 200
    assert client.get("/api/trips").json()["total"] == 1
    with factory() as db:
        for model in (ActionRecord, ProposalRecord, RunRecord, VersionRecord):
            assert db.scalar(select(func.count()).select_from(model).where(model.trip_id == trip_id)) == 0
        assert (
            db.scalar(
                select(func.count()).select_from(AgentTaskRecord).where(AgentTaskRecord.run_id.in_(run_ids))
            )
            == 0
        )
        assert db.get(TripRecord, other) is not None
        assert db.get(CatalogRecord, "shared").places == {"evidence": "keep"}


@pytest.mark.parametrize("value", [[{}], ["bad-theme"], "culture"])
def test_invalid_legacy_preference_is_validation_error(client, value):
    response = client.post("/api/trips", json={"start_date": "2026-10-26", "interests": value})
    assert response.status_code == 422


def test_delete_rejects_stale_version_and_running_task(client):
    trip_id = confirmed(client)
    endpoint = f"/api/trips/{trip_id}"
    assert client.request("DELETE", endpoint, json={"base_version": 2}).status_code == 409
    with client.app.state.session_factory.begin() as db:
        db.add(RunRecord(trip_id=trip_id, base_version=1, status="running"))
    assert client.request("DELETE", endpoint, json={"base_version": 1}).status_code == 409
    draft = client.post("/api/trips", json={"start_date": "2026-10-26"}).json()["id"]
    assert client.request("DELETE", f"/api/trips/{draft}", json={"base_version": 1}).status_code == 409
    assert client.request("DELETE", endpoint, json={"base_version": 0}).status_code == 422
