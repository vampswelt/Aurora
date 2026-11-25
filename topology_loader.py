"""
topology_loader.py : the ONLY code that reads the network-topology JSON.

Everything else (database seeder, LP solver, map API) receives a validated Topology object, so
swapping datasets means changing one path:

    TOPOLOGY_PATH=config/apple_global_topology.json     (environment variable), or
    load_topology("config/apple_global_topology.json")  (argument)

Path resolution order: argument -> TOPOLOGY_PATH -> config/demo_topology.json.

Validation reports ALL problems at once (not just the first) so a bad file fails fast at startup.
Check a file without touching the database:  python topology_loader.py config/demo_topology.json
"""
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join("config", "demo_topology.json")
ENV_VAR = "TOPOLOGY_PATH"

ROLES = ("SUPPLIER", "HUB", "ASSEMBLY")
MODES = ("air", "sea", "road", "rail")
ROUTE_STATUSES = ("ACTIVE", "DISRUPTED")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
RES_RE = re.compile(r"^[A-Z0-9_]{1,32}$")
SOLVER_DEFAULTS = {"shortage_penalty": 1000.0, "time_value_per_day": 0.0, "horizon_days": 1.0}


class TopologyError(Exception):
    """Raised with every validation problem found in the file."""
    def __init__(self, errors, path=None):
        self.errors = list(errors)
        self.path = path
        where = " (" + path + ")" if path else ""
        super().__init__("Invalid topology" + where + ":\n  - " + "\n  - ".join(self.errors))


@dataclass(frozen=True)
class Resource:
    id: str
    type: str = "GOODS"
    unit: str = "units"
    description: str = ""
    is_consumable: bool = True


@dataclass
class Node:
    id: str
    name: str
    role: str
    lat: float
    lon: float
    location: str = ""
    supply: dict = field(default_factory=dict)    # resource -> {"capacity_per_day", "unit_cost"}
    demand: dict = field(default_factory=dict)    # resource -> units per day
    storage: dict = field(default_factory=dict)   # resource -> storage capacity
    stock: dict = field(default_factory=dict)     # resource -> initial inventory


@dataclass(frozen=True)
class Mode:
    mode: str
    cost_per_unit: float
    transit_days: float
    capacity_per_day: float


@dataclass
class Route:
    id: str
    src: str
    dst: str
    status: str = "ACTIVE"
    modes: dict = field(default_factory=dict)     # mode name -> Mode


@dataclass
class Topology:
    meta: dict
    resources: dict
    nodes: dict
    routes: dict
    solver: dict
    warnings: list
    source_path: str = ""
    fingerprint: str = ""

    def public_dict(self):
        """JSON-safe view for the map/GUI API."""
        return {
            "meta": self.meta, "fingerprint": self.fingerprint,
            "source": os.path.basename(self.source_path), "solver": self.solver, "warnings": self.warnings,
            "resources": [{"id": r.id, "type": r.type, "unit": r.unit, "description": r.description}
                          for r in self.resources.values()],
            "nodes": [{"id": n.id, "name": n.name, "role": n.role, "lat": n.lat, "lon": n.lon,
                       "location": n.location, "supply": n.supply, "demand": n.demand,
                       "storage": n.storage, "stock": n.stock} for n in self.nodes.values()],
            "routes": [{"id": r.id, "from": r.src, "to": r.dst, "status": r.status,
                        "modes": {m: {"cost_per_unit": v.cost_per_unit, "transit_days": v.transit_days,
                                      "capacity_per_day": v.capacity_per_day} for m, v in r.modes.items()}}
                       for r in self.routes.values()],
        }


# ------------------------------------------------------------------ helpers
def _num(errors, where, value, minimum=0.0, strict=False):
    """Validate a finite number >= minimum (or > minimum when strict). Returns float or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        errors.append(f"{where} must be a number")
        return None
    if value < minimum or (strict and value == minimum):
        errors.append(f"{where} must be {'greater than' if strict else 'at least'} {minimum:g}")
        return None
    return float(value)


def _amounts(errors, where, obj, resources, field_name=None):
    """{'CHIPS': 100} or {'CHIPS': {field_name: 100}} -> {'CHIPS': 100.0}"""
    out = {}
    if obj is None:
        return out
    if not isinstance(obj, dict):
        errors.append(f"{where} must be an object of resource -> value")
        return out
    for res, val in obj.items():
        if res not in resources:
            errors.append(f"{where}: unknown resource '{res}'")
            continue
        if isinstance(val, dict) and field_name:
            val = val.get(field_name)
        n = _num(errors, f"{where}.{res}", val)
        if n is not None:
            out[res] = n
    return out


def _warn_unknown(warnings, where, obj, allowed):
    for key in obj:
        if key not in allowed:
            warnings.append(f"{where}: unknown field '{key}' ignored")


# ------------------------------------------------------------------ parsing
def parse_topology(data, source_path="<memory>", fingerprint=""):
    errors, warnings = [], []
    if not isinstance(data, dict):
        raise TopologyError(["top level must be a JSON object"], source_path)
    _warn_unknown(warnings, "top level", data, {"meta", "resources", "nodes", "routes", "solver"})
    for key in ("resources", "nodes", "routes"):
        if not isinstance(data.get(key), list) or not data.get(key):
            errors.append(f"'{key}' must be a non-empty list")
    if errors:
        raise TopologyError(errors, source_path)

    # resources
    resources = {}
    for i, r in enumerate(data["resources"]):
        where = f"resources[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where} must be an object")
            continue
        _warn_unknown(warnings, where, r, {"id", "type", "unit", "description", "is_consumable"})
        rid = r.get("id")
        if not isinstance(rid, str) or not RES_RE.match(rid):
            errors.append(f"{where}.id must be 1-32 characters from A-Z, 0-9, _")
            continue
        if rid in resources:
            errors.append(f"duplicate resource id '{rid}'")
            continue
        resources[rid] = Resource(id=rid, type=str(r.get("type", "GOODS")).upper()[:32],
                                  unit=str(r.get("unit", "units"))[:16],
                                  description=str(r.get("description", ""))[:255],
                                  is_consumable=bool(r.get("is_consumable", True)))

    # nodes
    nodes = {}
    for i, n in enumerate(data["nodes"]):
        where = f"nodes[{i}]"
        if not isinstance(n, dict):
            errors.append(f"{where} must be an object")
            continue
        _warn_unknown(warnings, where, n, {"id", "name", "role", "lat", "lon", "location",
                                           "supply", "demand", "storage", "stock"})
        nid = n.get("id")
        if not isinstance(nid, str) or not ID_RE.match(nid):
            errors.append(f"{where}.id must be 1-40 characters from letters, digits, _ . -")
            continue
        where = f"node '{nid}'"
        if nid in nodes:
            errors.append(f"duplicate node id '{nid}'")
            continue
        role = str(n.get("role", "")).upper()
        if role not in ROLES:
            errors.append(f"{where}: role must be one of {', '.join(ROLES)}")
        lat = _num(errors, f"{where}.lat", n.get("lat"), minimum=-90.0)
        lon = _num(errors, f"{where}.lon", n.get("lon"), minimum=-180.0)
        if lat is not None and lat > 90:
            errors.append(f"{where}.lat must be at most 90")
        if lon is not None and lon > 180:
            errors.append(f"{where}.lon must be at most 180")
        supply = {}
        raw_supply = n.get("supply")
        if raw_supply is not None and not isinstance(raw_supply, dict):
            errors.append(f"{where}.supply must be an object of resource -> {{capacity_per_day, unit_cost}}")
        else:
            for res, spec in (raw_supply or {}).items():
                if res not in resources:
                    errors.append(f"{where}.supply: unknown resource '{res}'")
                elif not isinstance(spec, dict):
                    errors.append(f"{where}.supply.{res} must be an object with capacity_per_day and unit_cost")
                else:
                    cap = _num(errors, f"{where}.supply.{res}.capacity_per_day", spec.get("capacity_per_day"))
                    cost = _num(errors, f"{where}.supply.{res}.unit_cost", spec.get("unit_cost"))
                    if cap is not None and cost is not None:
                        supply[res] = {"capacity_per_day": cap, "unit_cost": cost}
        nodes[nid] = Node(id=nid, name=str(n.get("name", nid))[:120], role=role,
                          lat=lat if lat is not None else 0.0, lon=lon if lon is not None else 0.0,
                          location=str(n.get("location", ""))[:128], supply=supply,
                          demand=_amounts(errors, f"{where}.demand", n.get("demand"), resources, "per_day"),
                          storage=_amounts(errors, f"{where}.storage", n.get("storage"), resources, "capacity"),
                          stock=_amounts(errors, f"{where}.stock", n.get("stock"), resources))

    # routes
    routes = {}
    for i, r in enumerate(data["routes"]):
        where = f"routes[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where} must be an object")
            continue
        _warn_unknown(warnings, where, r, {"id", "from", "to", "status", "modes"})
        rid = r.get("id")
        if not isinstance(rid, str) or not ID_RE.match(rid):
            errors.append(f"{where}.id must be 1-40 characters from letters, digits, _ . -")
            continue
        where = f"route '{rid}'"
        if rid in routes:
            errors.append(f"duplicate route id '{rid}'")
            continue
        src, dst = r.get("from"), r.get("to")
        for label, endpoint in (("from", src), ("to", dst)):
            if endpoint not in nodes:
                errors.append(f"{where}.{label}: unknown node '{endpoint}'")
        if src == dst:
            errors.append(f"{where}: 'from' and 'to' must differ")
        status = str(r.get("status", "ACTIVE")).upper()
        if status not in ROUTE_STATUSES:
            errors.append(f"{where}.status must be one of {', '.join(ROUTE_STATUSES)}")
        modes = {}
        raw_modes = r.get("modes")
        if not isinstance(raw_modes, dict) or not raw_modes:
            errors.append(f"{where}.modes must be a non-empty object (for example air, sea)")
        else:
            for mname, spec in raw_modes.items():
                if mname not in MODES:
                    errors.append(f"{where}.modes: unknown mode '{mname}' (use {', '.join(MODES)})")
                    continue
                if not isinstance(spec, dict):
                    errors.append(f"{where}.modes.{mname} must be an object")
                    continue
                cost = _num(errors, f"{where}.modes.{mname}.cost_per_unit", spec.get("cost_per_unit"))
                days = _num(errors, f"{where}.modes.{mname}.transit_days", spec.get("transit_days"), strict=True)
                cap = _num(errors, f"{where}.modes.{mname}.capacity_per_day", spec.get("capacity_per_day"),
                           strict=True)
                if None not in (cost, days, cap):
                    modes[mname] = Mode(mname, cost, days, cap)
        routes[rid] = Route(id=rid, src=src, dst=dst, status=status, modes=modes)

    # solver and meta
    solver = dict(SOLVER_DEFAULTS)
    raw_solver = data.get("solver", {})
    if not isinstance(raw_solver, dict):
        errors.append("'solver' must be an object")
    else:
        _warn_unknown(warnings, "solver", raw_solver, set(SOLVER_DEFAULTS))
        for key in SOLVER_DEFAULTS:
            if key in raw_solver:
                v = _num(errors, f"solver.{key}", raw_solver[key], strict=key in ("shortage_penalty", "horizon_days"))
                if v is not None:
                    solver[key] = v
    meta = data.get("meta", {})
    meta = {"name": str(meta.get("name", "unnamed")) if isinstance(meta, dict) else "unnamed",
            "currency": str(meta.get("currency", "USD")) if isinstance(meta, dict) else "USD",
            "description": str(meta.get("description", "")) if isinstance(meta, dict) else ""}

    if errors:
        raise TopologyError(errors, source_path)
    _add_warnings(warnings, resources, nodes, routes)
    return Topology(meta=meta, resources=resources, nodes=nodes, routes=routes, solver=solver,
                    warnings=warnings, source_path=source_path, fingerprint=fingerprint)


def _add_warnings(warnings, resources, nodes, routes):
    """Non-fatal data-quality hints."""
    linked = {r.src for r in routes.values()} | {r.dst for r in routes.values()}
    for nid in nodes:
        if nid not in linked:
            warnings.append(f"node '{nid}' has no routes and is isolated")
    adjacency = {}
    for r in routes.values():
        adjacency.setdefault(r.src, set()).add(r.dst)
    for res in resources:
        sources = [n.id for n in nodes.values() if n.supply.get(res, {}).get("capacity_per_day", 0) > 0]
        wanting = [n.id for n in nodes.values() if n.demand.get(res, 0) > 0]
        if wanting and not sources:
            warnings.append(f"resource '{res}' is demanded but no node supplies it")
            continue
        seen, queue = set(sources), list(sources)
        while queue:
            for nxt in adjacency.get(queue.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        for nid in wanting:
            if nid not in seen:
                warnings.append(f"node '{nid}' demands '{res}' but no supplier can reach it by any route")


# ------------------------------------------------------------------ loading
def resolve_path(path=None):
    chosen = path or os.getenv(ENV_VAR) or DEFAULT_PATH
    candidates = [chosen] if os.path.isabs(chosen) else [chosen, os.path.join(BASE_DIR, chosen)]
    for cand in candidates:
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    raise TopologyError([f"topology file not found: {chosen}"], chosen)


def load_topology(path=None):
    full = resolve_path(path)
    with open(full, "rb") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise TopologyError([f"not valid JSON: {e}"], full)
    return parse_topology(data, source_path=full, fingerprint=hashlib.sha256(raw).hexdigest())


def describe(topo):
    by_role = {}
    for n in topo.nodes.values():
        by_role[n.role] = by_role.get(n.role, 0) + 1
    roles = ", ".join(f"{count} {role.lower()}" for role, count in sorted(by_role.items()))
    lines = [f"Topology '{topo.meta['name']}' ({os.path.basename(topo.source_path)}, sha256 {topo.fingerprint[:12]})",
             f"  {len(topo.resources)} resources | {len(topo.nodes)} nodes ({roles}) | {len(topo.routes)} routes "
             f"({sum(len(r.modes) for r in topo.routes.values())} modes)"]
    lines += [f"  WARNING: {w}" for w in topo.warnings]
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        print(describe(load_topology(sys.argv[1] if len(sys.argv) > 1 else None)))
    except TopologyError as e:
        print(e)
        sys.exit(1)