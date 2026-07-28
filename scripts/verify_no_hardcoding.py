#!/usr/bin/env python3
"""Prove the answer is computed, not remembered.

Three independent checks, run in CI and live during the demo:

1. No demo entity name from the corpus appears anywhere in `src/`. If the code
   knew about "sarah@acme.com" or "NW-412" it could be cheating.
2. Every decision threshold comes from the intent profile. We grep the decision
   modules for numeric literals in comparisons.
3. A mutation test: perturb the corpus and confirm the verdicts move. Code that
   returns a memorised answer will not react to the data changing.

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ghostthread"
CORPUS = ROOT / "fixtures" / "corpus.json"

# Modules that decide verdicts or actions. These must contain no bare numeric
# thresholds; everything comes from the profile.
#
# router.py routes every complaint and memory.py sets the reply tone ladder, so
# both are decision surfaces and both are checked. If a threshold appears here,
# the "not hardcoded" claim is false.
DECISION_MODULES = ["leaks.py", "act.py", "router.py", "memory.py"]


def check_no_entity_names() -> list[str]:
    corpus = json.loads(CORPUS.read_text())
    names: set[str] = set()
    for row in corpus.get("identities", []):
        for key in ("email", "slack", "github", "display"):
            if row.get(key):
                names.add(str(row[key]))
    for row in corpus.get("complaints", []):
        names.add(row["id"])
    for row in corpus.get("work", []):
        names.add(row["id"])
        names.add(row["entity_id"])

    failures = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for name in names:
            if len(name) < 4:
                continue
            if re.search(rf"\b{re.escape(name)}\b", text):
                failures.append(f"{path.relative_to(ROOT)} mentions corpus entity {name!r}")
    return failures


def check_no_inline_thresholds() -> list[str]:
    """Flag comparisons against numeric literals in the decision modules.

    Structural constants (0, 1, indices, clamps) are allowed; anything that
    looks like a tunable threshold is not.
    """
    # Structural constants only: identity/clamp values, unit conversions, and
    # the HTTP success boundary. Nothing here influences a verdict.
    allowed = {0, 1, 2, 0.0, 1.0, 2.0, 60, 100, 300, 1000, 3600, 1e-6}
    failures = []
    for name in DECISION_MODULES:
        path = SRC / name
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for operand in [node.left, *node.comparators]:
                if isinstance(operand, ast.Constant) and isinstance(operand.value, (int, float)):
                    if operand.value not in allowed:
                        failures.append(
                            f"{path.relative_to(ROOT)}:{operand.lineno} compares against "
                            f"literal {operand.value!r} - should come from the intent profile"
                        )
    return failures


def check_reacts_to_data() -> list[str]:
    """Mutation test. Change the world, the answer must change with it."""
    sys.path.insert(0, str(ROOT / "src"))
    from ghostthread.pipeline import GhostThread  # noqa: E402

    baseline = GhostThread().run(act_on_leaks=False)
    base_leaks = {r["canonical_id"] for r in baseline.results if r["verdict"] == "leaked"}

    original = CORPUS.read_text()
    corpus = json.loads(original)
    # Track one currently-leaked complaint by adding matching work. If the
    # verdict does not flip, the answer was not being computed from the data.
    victim = next(
        c for c in corpus["complaints"]
        if f"{c['source']}:{c['id']}" in base_leaks
    )
    corpus["work"].append(
        {
            "id": "MUT-1",
            "source": "linear",
            "entity_id": "MUT-1",
            "reporter_email": victim["author_email"],
            "t_offset_hours": victim["t_offset_hours"] - 2,
            "status": "Todo",
            "title": victim["text"][:70],
            "description": victim["text"],
        }
    )
    failures = []
    try:
        CORPUS.write_text(json.dumps(corpus, indent=2))
        mutated = GhostThread().run(act_on_leaks=False)
        new_leaks = {r["canonical_id"] for r in mutated.results if r["verdict"] == "leaked"}
        key = f"{victim['source']}:{victim['id']}"
        # Assert on the specific complaint we mutated, not the aggregate count.
        # Retrieval ranking is not perfectly deterministic across runs, so a
        # global count comparison is flaky without proving anything extra - the
        # targeted flip is the actual claim.
        if key in new_leaks:
            failures.append(
                f"mutation test: added tracked work for {key} but it is still reported as leaked"
            )
    finally:
        CORPUS.write_text(original)
    return failures


def main() -> int:
    checks = [
        ("no demo entity names in source", check_no_entity_names),
        ("no inline decision thresholds", check_no_inline_thresholds),
        ("verdicts react to data changes", check_reacts_to_data),
    ]
    total = 0
    for label, fn in checks:
        failures = fn()
        total += len(failures)
        mark = "PASS" if not failures else "FAIL"
        print(f"[{mark}] {label}")
        for f in failures:
            print(f"       {f}")
    print()
    print("all checks passed" if total == 0 else f"{total} problem(s) found")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
