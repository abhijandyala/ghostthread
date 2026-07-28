PY := .venv/bin/python
export PYTHONPATH := src

.PHONY: demo verify killshot run seed test

demo:
	.venv/bin/uvicorn ghostthread.api:app --host 0.0.0.0 --port 8000 --reload

run:
	$(PY) -c "import json;from ghostthread.pipeline import GhostThread;print(json.dumps(GhostThread().run().to_dict()['summary'],indent=2))"

killshot:
	$(PY) -c "import json;from ghostthread.pipeline import GhostThread;from ghostthread.killshot import run_killshot;k=run_killshot(GhostThread());[print(f\"{r['scope_label']:34s} answerable={r['answerable_rate']*100:5.1f}%  leaks={r['leaks_reported']:2d}  false={len(r['false_leaks']):2d}  invisible={len(r['missed_invisible']):2d}  F1={r['f1']:.2f}\") for r in k['rows']];print();print(k['headline'])"

verify:
	$(PY) scripts/verify_no_hardcoding.py

seed:
	$(PY) scripts/seed_insforge.py

test: verify killshot
