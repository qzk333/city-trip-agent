"""Compare a frozen category/distance baseline against ranking and spatial planning.

All places and route estimates are self-authored fixtures, not live tourism evidence.
Run with backend/.venv's Python. No credentials or external requests are used.
"""

import json
from math import log2
from pathlib import Path
from time import perf_counter

from app.candidate_ranking import rank_candidates, select_for_requirements
from app.config import Settings
from app.planner import build_plan
from app.schemas import Place, PlaceTraits, Requirements
from app.spatial_planning import group_candidates
from app.tools import TravelTools
from app.travel_profile import normalize_profile
from app.validation import validate_itinerary

ROOT = Path(__file__).resolve().parents[1]


def ndcg(order, labels, k=6):
    def gain(values):
        return sum((2**value - 1) / log2(i + 2) for i, value in enumerate(values[:k]))

    ideal = gain(sorted(labels.values(), reverse=True))
    return round(gain([labels[pid] for pid in order]) / ideal, 4) if ideal else 1


def evaluate():
    dataset = json.loads(
        (ROOT / "evals/datasets/travel-quality-v1.json").read_text(encoding="utf-8")
    )
    catalog = {
        row["id"]: Place(
            id=row["id"],
            name=row["name"],
            subtitle="自建评测数据",
            longitude=row["longitude"],
            latitude=row["latitude"],
            category=row["category"],
            indoor=row["category"] == "culture",
            duration_minutes=row["duration_minutes"],
            opening=None,
            closing=None,
            description="自建夹具",
            source_url="",
            evidence_note="不是真实旅游资料",
            fixture=True,
            traits=PlaceTraits(themes=row["themes"]),
        )
        for row in dataset["places"]
    }
    rows = []
    for case in dataset["cases"]:
        req = Requirements(
            start_date="2026-10-26",
            lodging_mode="route_first",
            use_web_guides=False,
            **case["requirements"],
        )
        profile = normalize_profile(req.profile, req.note)
        labels = {
            pid: 2
            if set(p.traits.themes).intersection(case["relevant_themes"])
            else int(p.category in case["baseline_categories"])
            for pid, p in catalog.items()
        }
        baseline = sorted(
            catalog,
            key=lambda pid: catalog[pid].category not in case["baseline_categories"],
        )
        ranked = list(rank_candidates(catalog, profile))
        selected = select_for_requirements(req, catalog)
        options = {
            "day_groups": group_candidates(req, selected, list(selected)),
            "allowed_place_ids": list(selected),
            "preferred": [
                pid
                for g in group_candidates(req, selected, list(selected))
                for pid in g
            ],
        }
        variants = {}
        for name, visible, order, overrides in (
            ("baseline", catalog, baseline, {}),
            ("demand_spatial", selected, list(selected), options),
        ):
            started = perf_counter()
            tools = TravelTools(Settings(_env_file=None), req.city)
            plan = validate_itinerary(
                req,
                build_plan(req, order, tools, overrides, visible),
                overrides,
                visible,
            )
            variants[name] = {
                "status": plan.status,
                "errors": [c.code for c in plan.checks if c.severity == "error"],
                "activities": [
                    [a.place_id for a in day.activities] for day in plan.days
                ],
                "travel_minutes": sum(
                    leg.duration_minutes for day in plan.days for leg in day.legs
                ),
                "route_evaluations": tools.route_calls,
                "elapsed_ms": round((perf_counter() - started) * 1000, 2),
            }
        variants["demand_spatial"]["expected_outcome"] = bool(
            variants["demand_spatial"]["errors"]
        ) == case.get("expected_blocked", False)
        rows.append(
            {
                "id": case["id"],
                "baseline_ndcg_at_6": ndcg(baseline, labels),
                "demand_ndcg_at_6": ndcg(ranked, labels),
                **variants,
            }
        )
    summary = {"dataset": dataset["version"], "scope": dataset["scope"], "cases": rows}
    summary["expected_outcomes_passed"] = sum(
        row["demand_spatial"]["expected_outcome"] for row in rows
    )
    for strategy in ("baseline", "demand_spatial"):
        summary[strategy] = {
            "valid_plans": sum(not row[strategy]["errors"] for row in rows),
            "total_route_evaluations": sum(
                row[strategy]["route_evaluations"] for row in rows
            ),
            "total_travel_minutes": sum(
                row[strategy]["travel_minutes"] for row in rows
            ),
        }
    summary["mean_ndcg_at_6"] = {
        strategy: round(
            sum(row[f"{strategy}_ndcg_at_6"] for row in rows) / len(rows), 4
        )
        for strategy in ("baseline", "demand")
    }
    output = ROOT / "evals/results/travel-quality-v1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "cases"},
            ensure_ascii=False,
        )
    )
    print(f"Per-case results: {output}")
    if summary["expected_outcomes_passed"] != len(rows):
        raise SystemExit("Unexpected validation outcomes; inspect per-case results.")


if __name__ == "__main__":
    evaluate()
