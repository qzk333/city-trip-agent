from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", env_file_encoding="utf-8")
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'local.sqlite3').as_posix()}"
    tool_provider: Literal["demo", "amap"] = "demo"
    planning_mode: Literal["demo", "llm"] = "demo"
    amap_api_key: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    tool_timeout_seconds: float = Field(default=12, gt=0, le=30)
    max_route_calls: int = Field(default=100, ge=1, le=500)
    max_run_seconds: float = Field(default=60, gt=0, le=180)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


def get_settings() -> Settings:
    return Settings()
