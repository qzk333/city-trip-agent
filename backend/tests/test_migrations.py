from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import ROOT


def test_initial_migration_matches_models_and_is_repeatable(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "backend/alembic.ini"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(url)
    assert {"trips", "versions", "proposals", "runs", "actions"} <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_candidate_metadata_upgrade_preserves_legacy_snapshot(tmp_path):
    import json

    from sqlalchemy import text

    url = f"sqlite:///{(tmp_path / 'legacy.sqlite3').as_posix()}"
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0005")
    engine = create_engine(url)
    original = {"legacy": {"name": "legacy evidence"}}
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO catalogs (id, city_id, places, created_at) "
                "VALUES ('legacy', 19598, :places, '2026-10-26')"
            ),
            {"places": json.dumps(original)},
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(text("SELECT places, discovery FROM catalogs WHERE id='legacy'")).one()
        assert json.loads(row.places) == original and json.loads(row.discovery) == {}
    engine.dispose()


def test_unified_themes_migration_preserves_all_snapshots_and_evidence(tmp_path):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.database import CatalogRecord, ProposalRecord, TripRecord, VersionRecord

    url = f"sqlite:///{(tmp_path / 'themes.sqlite3').as_posix()}"
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0006")
    engine = create_engine(url)
    original = {"start_date": "2026-10-26", "interests": ["nature", "culture"], "note": "旧偏好"}
    explicit = {**original, "profile": {"themes": ["night"], "walking": "less"}}
    with Session(engine) as db, db.begin():
        db.add(
            TripRecord(id="trip", requirements=explicit, current_version=1, catalog={"p": {"source": "keep"}})
        )
        db.add(
            VersionRecord(
                id="version",
                trip_id="trip",
                number=1,
                requirements=original,
                itinerary={"days": []},
                catalog={"historic": {}},
                label="保留版本",
            )
        )
        db.add(
            ProposalRecord(
                id="proposal",
                trip_id="trip",
                base_version=1,
                requirements=explicit,
                itinerary={"days": []},
                label="保留预览",
            )
        )
        db.add(
            ProposalRecord(
                id="null", trip_id="trip", base_version=1, requirements=None, itinerary={}, label="无快照"
            )
        )
        db.add(
            CatalogRecord(
                id="catalog",
                city_id=19598,
                places={"evidence": "keep"},
                discovery={"fingerprint": "old", "queries": ["博物馆"]},
            )
        )
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with Session(engine) as db:
        trip = db.get(TripRecord, "trip")
        version = db.get(VersionRecord, "version")
        proposal = db.get(ProposalRecord, "proposal")
        assert trip.requirements == {
            "start_date": "2026-10-26",
            "note": "旧偏好",
            "profile": {"themes": ["night"], "walking": "less"},
        }
        assert proposal.requirements == trip.requirements
        assert version.requirements == {
            "start_date": "2026-10-26",
            "note": "旧偏好",
            "profile": {"themes": ["nature", "culture"]},
        }
        assert trip.current_version == 1 and trip.catalog == {"p": {"source": "keep"}}
        assert version.catalog == {"historic": {}} and version.label == "保留版本"
        assert proposal.itinerary == {"days": []} and proposal.applied_version is None
        assert db.get(ProposalRecord, "null").requirements is None
        catalog = db.scalar(select(CatalogRecord))
        assert catalog.places == {"evidence": "keep"}
        assert catalog.discovery == {
            "fingerprint": None,
            "queries": ["博物馆"],
            "preference_schema_version": 2,
        }
    engine.dispose()
