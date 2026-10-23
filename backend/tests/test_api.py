def create_and_plan(client, **overrides):
    body = {"start_date": "2026-10-24", "must_visit": ["westlake"], **overrides}
    created = client.post("/api/trips", json=body)
    assert created.status_code == 201
    trip_id = created.json()["id"]
    run_id = client.post(f"/api/trips/{trip_id}/plan-runs").json()["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed", run
    return trip_id, run["proposal"]


def apply(client, trip_id, proposal, key="first-confirm-key"):
    return client.post(
        f"/api/trips/{trip_id}/proposals/{proposal['id']}/apply",
        json={
            "base_version": proposal["base_version"],
            "idempotency_key": key,
        },
    )


def test_generation_is_a_preview_until_user_confirms(client):
    trip_id, proposal = create_and_plan(client)
    assert client.get(f"/api/trips/{trip_id}").json()["current_version"] == 0
    assert apply(client, trip_id, proposal).json()["version"] == 1
    saved = client.get(f"/api/trips/{trip_id}").json()
    assert saved["itinerary"]["days"] == proposal["itinerary"]["days"]


def test_confirmation_replay_and_stale_proposals(client):
    trip_id, first = create_and_plan(client)
    second_run = client.post(f"/api/trips/{trip_id}/plan-runs").json()["run_id"]
    second = client.get(f"/api/runs/{second_run}").json()["proposal"]
    assert apply(client, trip_id, first).status_code == 200
    replay = apply(client, trip_id, first)
    assert replay.json() == {"version": 1, "replayed": True}
    assert apply(client, trip_id, second, "different-confirm-key").status_code == 409
    assert len(client.get(f"/api/trips/{trip_id}/versions").json()) == 1


def test_conflicted_proposal_cannot_be_committed(client):
    trip_id, proposal = create_and_plan(client, day_start="09:00", day_end="09:10")
    assert apply(client, trip_id, proposal).status_code == 422
    assert client.get(f"/api/trips/{trip_id}").json()["current_version"] == 0


def test_lock_delay_and_restore_preserve_fixed_appointments(client):
    trip_id, initial = create_and_plan(
        client,
        appointments=[
            {"place_id": "gushan_museum", "start": "14:00", "end": "15:30"},
        ],
    )
    assert apply(client, trip_id, initial).status_code == 200
    delayed = client.post(
        f"/api/trips/{trip_id}/proposals",
        json={
            "base_version": 1,
            "action": "delay",
            "day": 1,
            "delay_minutes": 40,
        },
    )
    run = client.get(f"/api/runs/{delayed.json()['run_id']}").json()
    assert run["status"] == "completed", run
    fixed = next(
        a for d in run["proposal"]["itinerary"]["days"] for a in d["activities"] if "-fixed" in a["id"]
    )
    assert fixed["start"] == "14:00"
    assert apply(client, trip_id, run["proposal"], "delay-confirm-key").status_code == 200
    restored = client.post(
        f"/api/trips/{trip_id}/versions/1/restore",
        json={
            "base_version": 2,
            "idempotency_key": "restore-history-key",
        },
    )
    assert restored.json()["version"] == 3
    assert client.get(f"/api/trips/{trip_id}").json()["itinerary"] == initial["itinerary"]


def test_locked_and_required_activity_removal_rejected(client):
    trip_id, proposal = create_and_plan(client)
    apply(client, trip_id, proposal)
    required = next(
        a for d in proposal["itinerary"]["days"] for a in d["activities"] if a["place_id"] == "westlake"
    )
    result = client.post(
        f"/api/trips/{trip_id}/proposals",
        json={
            "base_version": 1,
            "action": "remove",
            "day": required["day"],
            "activity_id": required["id"],
        },
    )
    assert result.status_code == 409


def test_sse_replays_only_events_after_the_cursor(client):
    created = client.post("/api/trips", json={"start_date": "2026-10-24"}).json()
    run_id = client.post(f"/api/trips/{created['id']}/plan-runs").json()["run_id"]
    events = client.get(f"/api/runs/{run_id}/events?after=2").text
    assert "id: 1\n" not in events
    assert "event: done" in events


def test_unknown_places_are_not_accepted(client):
    response = client.post("/api/trips", json={"start_date": "2026-10-24", "must_visit": ["invented"]})
    assert response.status_code == 422


def test_dismissed_proposal_cannot_be_applied(client):
    trip_id, proposal = create_and_plan(client)
    result = client.post(f"/api/trips/{trip_id}/proposals/{proposal['id']}/dismiss")
    assert result.status_code == 200
    assert apply(client, trip_id, proposal).status_code == 409


def test_repeated_delays_accumulate_in_persisted_state(client):
    trip_id, proposal = create_and_plan(client)
    apply(client, trip_id, proposal)
    for index in (1, 2):
        run_id = client.post(
            f"/api/trips/{trip_id}/proposals",
            json={
                "base_version": index,
                "action": "delay",
                "delay_minutes": 20,
            },
        ).json()["run_id"]
        proposal = client.get(f"/api/runs/{run_id}").json()["proposal"]
        assert proposal["itinerary"]["day_start_offsets"] == {"1": index * 20}
        assert apply(client, trip_id, proposal, f"delay-number-{index}").status_code == 200


def test_removing_an_optional_activity_does_not_add_an_unrequested_replacement(client):
    trip_id, initial = create_and_plan(client)
    apply(client, trip_id, initial)
    activities = initial["itinerary"]["days"][0]["activities"]
    target = next(a for a in activities if a["place_id"] != "westlake")
    run_id = client.post(
        f"/api/trips/{trip_id}/proposals",
        json={
            "base_version": 1,
            "action": "remove",
            "activity_id": target["id"],
        },
    ).json()["run_id"]
    proposal = client.get(f"/api/runs/{run_id}").json()["proposal"]
    remaining = proposal["itinerary"]["days"][0]["activities"]
    assert {a["id"] for a in remaining} == {a["id"] for a in activities} - {target["id"]}
    assert apply(client, trip_id, proposal, "remove-confirm-key").status_code == 200


def test_replanning_does_not_duplicate_places_from_frozen_future_days(client):
    trip_id, initial = create_and_plan(client, days=3)
    apply(client, trip_id, initial)
    run_id = client.post(
        f"/api/trips/{trip_id}/proposals",
        json={
            "base_version": 1,
            "action": "delay",
            "day": 1,
            "delay_minutes": 20,
        },
    ).json()["run_id"]
    proposal = client.get(f"/api/runs/{run_id}").json()["proposal"]
    assert proposal["itinerary"]["days"][1:] == initial["itinerary"]["days"][1:]
    visited = [a["place_id"] for d in proposal["itinerary"]["days"] for a in d["activities"]]
    assert len(visited) == len(set(visited))
