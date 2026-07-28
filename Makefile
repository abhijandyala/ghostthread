# Windows venvs put the interpreter in Scripts/, POSIX ones in bin/. Detect
# rather than picking one, because this repo gets built on both.
#
# `=` and not `:=`: immediate expansion runs the detection when the Makefile is
# parsed, which is before the `venv` target has created anything, so `make venv
# smoke` resolves the interpreter against a directory that does not exist yet
# and every later target uses the wrong path. Lazy expansion re-runs the check
# at each use, after the venv exists.
PY = $(shell if [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe; else echo .venv/bin/python; fi)
UVICORN = $(shell if [ -x .venv/Scripts/uvicorn.exe ]; then echo .venv/Scripts/uvicorn.exe; else echo .venv/bin/uvicorn; fi)
export PYTHONPATH := src

.PHONY: demo verify killshot run seed test smoke demo-ready eval venv

venv:
	python -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	@echo "venv ready"

demo:
	$(UVICORN) ghostthread.api:app --host 0.0.0.0 --port 8000 --reload

run:
	$(PY) -c "import json;from ghostthread.pipeline import GhostThread;print(json.dumps(GhostThread().run().to_dict()['summary'],indent=2))"

killshot:
	$(PY) -c "import json;from ghostthread.pipeline import GhostThread;from ghostthread.killshot import run_killshot;k=run_killshot(GhostThread());[print(f\"{r['scope_label']:34s} answerable={r['answerable_rate']*100:5.1f}%  leaks={r['leaks_reported']:2d}  false={len(r['false_leaks']):2d}  invisible={len(r['missed_invisible']):2d}  F1={r['f1']:.2f}\") for r in k['rows']];print();print(k['headline'])"

verify:
	$(PY) scripts/verify_no_hardcoding.py

# The sync-point check. Run before every merge to main.
smoke:
	$(PY) scripts/smoke.py

# The pre-stage check. Same as smoke, but a stubbed node is a failure.
demo-ready:
	$(PY) scripts/smoke.py --demo-ready

# The pre-rehearsal check. Asserts the grounding invariants against the live
# tenant, and proves it can fail via the negative controls.
eval:
	$(PY) scripts/eval.py

seed:
	$(PY) scripts/seed_insforge.py

test: smoke verify killshot
