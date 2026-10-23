import json

from app.config import ROOT
from app.schemas import Place


def load_catalog() -> dict[str, Place]:
    raw = json.loads((ROOT / "data/curated/hangzhou.demo.json").read_text(encoding="utf-8"))
    return {p["id"]: Place(**p, source_url="", evidence_note=raw["notice"]) for p in raw["places"]}


CATALOG = load_catalog()
