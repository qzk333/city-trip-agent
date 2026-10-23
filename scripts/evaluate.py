"""Run the deterministic smoke set. Execute with backend/.venv's Python."""

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from app.agent import generate
from app.config import Settings
from app.schemas import Requirements

root = Path(__file__).resolve().parents[1]
dataset = json.loads((root / "evals/datasets/smoke.json").read_text(encoding="utf-8"))
rows = []
for case in dataset["cases"]:
    started = perf_counter()
    result = generate(
        Requirements(**case["requirements"]),
        Settings(
            tool_provider="demo",
            planning_mode="demo",
            _env_file=None,
            **case.get("settings", {}),
        ),
        lambda *_: None,
    )
    rows.append(
        {
            "id": case["id"],
            "expected": case["expected"],
            "actual": result.status,
            "passed": result.status == case["expected"],
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
            "tool_records": len(result.trace),
            "errors": [c.code for c in result.checks if c.severity == "error"],
        }
    )
    print(f"{'PASS' if rows[-1]['passed'] else 'FAIL'} {case['id']}: {result.status}")
report = {
    "dataset_version": dataset["version"],
    "provider": "demo",
    "planner": "heuristic-v1",
    "run_at": datetime.now(UTC).isoformat(),
    "passed": sum(r["passed"] for r in rows),
    "total": len(rows),
    "cases": rows,
}
output = root / "evals/results/smoke.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{report['passed']}/{report['total']} passed; report: evals/results/smoke.json")
raise SystemExit(0 if all(r["passed"] for r in rows) else 1)
