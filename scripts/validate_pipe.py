#!/usr/bin/env python3
"""Validate rocketride/ghostthread.pipe against RocketRide's live node catalog.

    PYTHONPATH=src python scripts/validate_pipe.py
    PYTHONPATH=src python scripts/validate_pipe.py --json          # machine-readable
    PYTHONPATH=src python scripts/validate_pipe.py --describe agent_rocketride

Where the schema comes from
---------------------------
`GET https://api.rocketride.ai/services` returns the authoritative catalogue --
138 providers, each with the JSON Schema for its `config` block, its lane map,
and (for agents) the cardinality of the control-plane children it requires. This
is the same data the VS Code extension writes to `.rocketride/services-catalog
.json`; fetching it live means this check cannot go stale against a checked-in
copy.

Checked here
------------
1. Top-level shape, including `source` -- the entry-point component id. The SDK's
   PipelineConfig has it and a pipeline without one has no declared trigger.
2. Every provider exists in the catalogue.
3. Every `config` validates against that provider's `Pipe.schema`: required
   properties present, no invented properties, types and enums honoured.
   Unknown properties are an error rather than a warning, because a key the
   engine does not read is a setting that silently does nothing -- which is
   exactly how the previous version of this file specified an HTTP method, a URL
   and a request body that `tool_http_request` never had.
4. Lane wiring: for `{"lane": L, "from": F}`, L must be an output lane of F's
   provider and an input lane of the consumer's provider.
5. Control wiring: `classType` must be one of the parent agent's `invoke`
   channels, and each channel's min/max cardinality must be satisfied.
   `agent_rocketride` requires exactly one `llm` and exactly one `memory`.
6. `${...}` placeholders. The server substitutes `ROCKETRIDE_*` account
   environment variables and nothing else, so any other placeholder is a value
   that will reach the engine as a literal dollar-brace string.

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghostthread import config  # noqa: E402

PIPE_PATH = ROOT / "rocketride" / "ghostthread.pipe"
CATALOG_PATH = "/services"
PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_]+)\}")

# The engine resolves only these. See rocketride/client.py: "${ROCKETRIDE_*}
# placeholders are NOT substituted [in returned pipelines], so no secrets are
# included in the response" -- they are resolved server-side, from the account
# environment, at run time.
SUBSTITUTED_PREFIX = "ROCKETRIDE_"


def fetch_catalog(uri: str) -> dict[str, Any]:
    resp = httpx.get(uri.rstrip("/") + CATALOG_PATH, timeout=60.0)
    resp.raise_for_status()
    body = resp.json()
    services = (body.get("data") or {}).get("services")
    if not isinstance(services, dict):
        raise RuntimeError(f"{uri}{CATALOG_PATH} did not return a service catalogue")
    return services


# --- config validation --------------------------------------------------------


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def validate_config(component_id: str, schema: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    """Enough JSON Schema to catch the mistakes that actually happen here."""
    problems: list[str] = []
    props: dict[str, Any] = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []
    # `dependencies` is how the catalogue expresses "profile picks a sub-object",
    # e.g. llm_anthropic. The chosen key is legal as a config property.
    dependencies: dict[str, Any] = schema.get("dependencies") or {}

    allowed = set(props)
    for dep in dependencies.values():
        for variant in dep.get("oneOf") or []:
            allowed.update((variant.get("properties") or {}).keys())

    for key in required:
        if key not in cfg:
            problems.append(f"{component_id}: config is missing required property {key!r}")

    for key, value in cfg.items():
        if key not in allowed:
            problems.append(
                f"{component_id}: config has no such property {key!r} "
                f"(this provider accepts {', '.join(sorted(allowed)) or 'nothing'})"
            )
            continue
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if isinstance(expected, str) and not _type_ok(value, expected):
            problems.append(
                f"{component_id}: config.{key} should be {expected}, got {type(value).__name__}"
            )
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            problems.append(
                f"{component_id}: config.{key}={value!r} is not one of {enum}"
            )
        if expected == "array" and isinstance(value, list):
            item_type = ((spec.get("items") or {}).get("type"))
            if isinstance(item_type, str):
                for i, item in enumerate(value):
                    if not _type_ok(item, item_type):
                        problems.append(
                            f"{component_id}: config.{key}[{i}] should be {item_type}"
                        )
        if isinstance(expected, str) and expected in ("number", "integer"):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "minimum" in spec and value < spec["minimum"]:
                    problems.append(f"{component_id}: config.{key}={value} is below minimum {spec['minimum']}")
                if "maximum" in spec and value > spec["maximum"]:
                    problems.append(f"{component_id}: config.{key}={value} is above maximum {spec['maximum']}")

    return problems


# --- graph validation ---------------------------------------------------------


def output_lanes(service: dict[str, Any]) -> set[str]:
    """Lanes this provider can emit.

    A source declares its repertoire under the `_source` pseudo-lane; everything
    else declares outputs as the values of its input->outputs map.
    """
    lanes = service.get("lanes") or {}
    if "_source" in lanes:
        return set(lanes["_source"] or [])
    out: set[str] = set()
    for produced in lanes.values():
        out.update(produced or [])
    return out


def input_lanes(service: dict[str, Any]) -> set[str]:
    lanes = service.get("lanes") or {}
    return {name for name in lanes if name != "_source"}


def validate(pipe: dict[str, Any], catalog: dict[str, Any]) -> list[str]:
    problems: list[str] = []

    components = pipe.get("components")
    if not isinstance(components, list) or not components:
        return ["pipeline has no `components` array"]

    by_id: dict[str, dict[str, Any]] = {}
    for i, comp in enumerate(components):
        cid = comp.get("id")
        if not cid:
            problems.append(f"component #{i} has no `id`")
            continue
        if cid in by_id:
            problems.append(f"duplicate component id {cid!r}")
        by_id[cid] = comp

    # 1. entry point
    source = pipe.get("source")
    if not source:
        problems.append(
            "pipeline has no `source`: PipelineConfig.source names the entry-point "
            "component, and without it the trigger is undeclared"
        )
    elif source not in by_id:
        problems.append(f"`source` names {source!r}, which is not a component")
    else:
        service = catalog.get(by_id[source].get("provider"))
        if service and "source" not in (service.get("classType") or []):
            problems.append(
                f"`source` names {source!r}, whose provider "
                f"{by_id[source].get('provider')!r} is not classType 'source'"
            )

    # 2 + 3. providers and their config
    for cid, comp in by_id.items():
        provider = comp.get("provider")
        service = catalog.get(provider)
        if service is None:
            problems.append(f"{cid}: no such provider {provider!r} in the RocketRide catalogue")
            continue
        schema = ((service.get("Pipe") or {}).get("schema")) or {}
        cfg = comp.get("config")
        if not isinstance(cfg, dict):
            problems.append(f"{cid}: `config` must be an object")
            continue
        problems.extend(validate_config(cid, schema, cfg))

    # 4. lanes
    for cid, comp in by_id.items():
        service = catalog.get(comp.get("provider"))
        if service is None:
            continue
        accepts = input_lanes(service)
        for edge in comp.get("input") or []:
            lane, upstream = edge.get("lane"), edge.get("from")
            if upstream not in by_id:
                problems.append(f"{cid}: input references unknown component {upstream!r}")
                continue
            up_service = catalog.get(by_id[upstream].get("provider"))
            if up_service is None:
                continue
            emits = output_lanes(up_service)
            if lane not in emits:
                problems.append(
                    f"{cid}: input lane {lane!r} from {upstream!r} -- "
                    f"{by_id[upstream].get('provider')} emits {sorted(emits) or 'nothing'}"
                )
            if accepts and lane not in accepts:
                problems.append(
                    f"{cid}: consumes lane {lane!r} but {comp.get('provider')} "
                    f"accepts {sorted(accepts)}"
                )

    # 5. control plane
    attached: dict[str, dict[str, int]] = {}
    for cid, comp in by_id.items():
        for edge in comp.get("control") or []:
            parent, class_type = edge.get("from"), edge.get("classType")
            if parent not in by_id:
                problems.append(f"{cid}: control references unknown component {parent!r}")
                continue
            parent_service = catalog.get(by_id[parent].get("provider")) or {}
            invoke = parent_service.get("invoke") or {}
            if class_type not in invoke:
                problems.append(
                    f"{cid}: control classType {class_type!r} is not an invoke channel of "
                    f"{parent!r} ({sorted(invoke) or 'that provider invokes nothing'})"
                )
                continue
            own = catalog.get(comp.get("provider")) or {}
            if class_type not in (own.get("classType") or []):
                problems.append(
                    f"{cid}: attached to {parent!r} as {class_type!r} but "
                    f"{comp.get('provider')} is classType {own.get('classType')}"
                )
            attached.setdefault(parent, {}).setdefault(class_type, 0)
            attached[parent][class_type] += 1

    for cid, comp in by_id.items():
        invoke = (catalog.get(comp.get("provider")) or {}).get("invoke") or {}
        counts = attached.get(cid, {})
        for channel, spec in invoke.items():
            n = counts.get(channel, 0)
            low, high = spec.get("min"), spec.get("max")
            if low is not None and n < low:
                problems.append(
                    f"{cid}: needs at least {low} {channel!r} node(s) attached via "
                    f"control, found {n} -- {spec.get('description', '')}".rstrip()
                )
            if high is not None and n > high:
                problems.append(f"{cid}: accepts at most {high} {channel!r} node(s), found {n}")

    return problems


def placeholders(pipe: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(substituted-by-the-server, will-arrive-as-a-literal-string)."""
    found = set(PLACEHOLDER.findall(json.dumps(pipe)))
    resolved = sorted(n for n in found if n.startswith(SUBSTITUTED_PREFIX))
    literal = sorted(n for n in found if not n.startswith(SUBSTITUTED_PREFIX))
    return resolved, literal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipe", default=str(PIPE_PATH))
    parser.add_argument("--uri", default=None, help="RocketRide API base (default ROCKETRIDE_URI)")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument("--describe", help="print one provider's schema and lanes, then exit")
    args = parser.parse_args()

    uri = args.uri or config.ROCKETRIDE_URI
    try:
        catalog = fetch_catalog(uri)
    except Exception as exc:
        print(f"could not fetch the node catalogue from {uri}{CATALOG_PATH}: {exc}", file=sys.stderr)
        return 2

    if args.describe:
        service = catalog.get(args.describe)
        if service is None:
            near = sorted(n for n in catalog if args.describe.split("_")[0] in n)
            print(f"no provider {args.describe!r}. Similar: {near or 'none'}", file=sys.stderr)
            return 2
        print(json.dumps({
            "classType": service.get("classType"),
            "lanes": service.get("lanes"),
            "invoke": service.get("invoke"),
            "schema": (service.get("Pipe") or {}).get("schema"),
        }, indent=2))
        return 0

    pipe = json.loads(Path(args.pipe).read_text(encoding="utf-8"))
    problems = validate(pipe, catalog)
    resolved, literal = placeholders(pipe)
    for name in literal:
        problems.append(
            f"placeholder ${{{name}}} will not be substituted: the engine resolves "
            f"{SUBSTITUTED_PREFIX}* account variables only, so this reaches the node "
            f"as the literal text '${{{name}}}'"
        )

    if args.json:
        print(json.dumps({
            "pipe": args.pipe,
            "catalog_size": len(catalog),
            "problems": problems,
            "placeholders_resolved_by_server": resolved,
        }, indent=2))
        return 1 if problems else 0

    print(f"catalogue      {len(catalog)} providers from {uri}{CATALOG_PATH}")
    print(f"pipeline       {args.pipe}")
    print(f"components     {len(pipe.get('components') or [])}, source={pipe.get('source')!r}")
    if resolved:
        print(f"placeholders   {', '.join(resolved)}")
        print("               (resolved server-side from the RocketRide account environment;")
        print("                nothing secret is stored in this repository)")
    print()
    if problems:
        print(f"[FAIL] {len(problems)} problem(s):")
        for p in problems:
            print(f"       {p}")
        return 1
    print("[PASS] the pipeline validates against the live node catalogue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
