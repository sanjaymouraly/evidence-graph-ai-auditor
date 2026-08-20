#!/usr/bin/env python3
"""Run the synthetic evidence benchmark outside the API and archive the artefacts.

Usage (from the repository root):

    PYTHONPATH=backend python scripts/run_benchmark.py                 # dev + held-out
    PYTHONPATH=backend python scripts/run_benchmark.py --dataset dev   # one split
    PYTHONPATH=backend python scripts/run_benchmark.py --no-save       # print only

Results are written to ``evaluation/benchmarks/<dataset>-<timestamp>.json`` together with a
small ``latest.json`` pointer per split. The run is executed against a throwaway database, so
it never mutates the application's working data.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.models import Base, User, Workspace  # noqa: E402
from app.services.benchmark import TARGETS, run_benchmark  # noqa: E402

OUT_DIR = REPO_ROOT / "evaluation" / "benchmarks"


def git_revision() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                                       text=True, stderr=subprocess.DEVNULL).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def scratch_session(tmp_db: Path):
    engine = create_engine(f"sqlite:///{tmp_db}", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    db = factory()
    workspace = Workspace(name="Evaluation harness", slug=f"eval-{tmp_db.stem}", settings={"harness": True})
    db.add(workspace)
    db.flush()
    user = User(workspace_id=workspace.id, email="eval@example.invalid", name="Evaluation Harness",
                role="admin", password_hash=hash_password("unused-password"), is_active=True)
    db.add(user)
    db.commit()
    return db, user


def summarise(result: dict) -> str:
    lines = [f"  status           : {result['status']}  ({result['duration_seconds']}s)"]
    for name, value in result["metrics"].items():
        if isinstance(value, float):
            lines.append(f"  {name:<24}: {value:.4f}")
    for name, target in result["targets"].items():
        mark = "PASS" if target["pass"] else "FAIL"
        lines.append(f"  target {name:<16}: {target['actual']:.4f} / {target['target']:.2f}  [{mark}]")
    if result["errors"]:
        lines.append(f"  errors           : {len(result['errors'])}")
        for error in result["errors"][:5]:
            lines.append(f"    - {error}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["dev", "heldout", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N decisions")
    parser.add_argument("--no-save", action="store_true", help="Do not write JSON artefacts")
    args = parser.parse_args()

    datasets = ["dev", "heldout"] if args.dataset == "all" else [args.dataset]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scratch = OUT_DIR / ".scratch"
    if scratch.exists():
        shutil.rmtree(scratch)  # every invocation starts from an empty scratch database
    scratch.mkdir(parents=True)
    db, user = scratch_session(scratch / "harness.db")

    failures = 0
    try:
        for dataset in datasets:
            print(f"\n=== {dataset} ===")
            result = run_benchmark(db, user, dataset, limit=args.limit)
            result["harness"] = {
                "git_revision": git_revision(),
                "python": platform.python_version(),
                "platform": platform.platform(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "scratch_db": str(scratch / "harness.db"),
            }
            print(summarise(result))
            failures += sum(1 for t in result["targets"].values() if not t["pass"])
            failures += len(result["errors"])
            if not args.no_save:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                path = OUT_DIR / f"{dataset}-{stamp}.json"
                path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
                (OUT_DIR / f"latest-{dataset}.json").write_text(
                    json.dumps(result, indent=2, default=str), encoding="utf-8")
                print(f"  saved            : {path.relative_to(REPO_ROOT)}")
    finally:
        db.close()

    print(f"\nTargets: {json.dumps({k: v for k, v in TARGETS.items()})}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
