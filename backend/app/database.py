from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import ROOT, Settings


def new_id() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class TripRecord(Base):
    __tablename__ = "trips"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    requirements: Mapped[dict] = mapped_column(JSON)
    catalog: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class VersionRecord(Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("trip_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    itinerary: Mapped[dict] = mapped_column(JSON)
    requirements: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    catalog: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    label: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class RunRecord(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    base_version: Mapped[int] = mapped_column(Integer)
    events: Mapped[list] = mapped_column(JSON, default=list)
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class AgentTaskRecord(Base):
    __tablename__ = "agent_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="running")
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[str] = mapped_column(String(40), default=now)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ProposalRecord(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    base_version: Mapped[int] = mapped_column(Integer)
    itinerary: Mapped[dict] = mapped_column(JSON)
    requirements: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    catalog: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    label: Mapped[str] = mapped_column(String(200))
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)


class ActionRecord(Base):
    __tablename__ = "actions"
    __table_args__ = (UniqueConstraint("trip_id", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)


class CatalogRecord(Base):
    __tablename__ = "catalogs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    city_id: Mapped[int] = mapped_column(Integer)
    places: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


def build_database(settings: Settings):
    (ROOT / "data").mkdir(exist_ok=True)
    sqlite = settings.database_url.startswith("sqlite")
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if sqlite else {})
    return engine, sessionmaker(engine, expire_on_commit=False)
