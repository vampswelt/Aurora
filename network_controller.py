"""
network_controller.py : the "brain" between the topology, the LP solver and the GUI API.

Holds the current disruptions (nodes, routes, transport modes), re-solves the network after every
change, and keeps an event log. Thread-safe. Database persistence is optional: pass an object with
record_event(type, description, severity) and set_route_status(code, status). Persistence failures
are logged and never break the plan.
"""
import logging
import threading
import time
from collections import deque
from datetime import datetime

from network_optimizer import solve_network
from topology_loader import MODES

log = logging.getLogger("controller")
TARGETS = ("node", "route", "mode")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


class NetworkController:
    def __init__(self, topology, persist=None):
        self.topology = topology
        self.persist = persist
        self.lock = threading.RLock()
        self.disabled_nodes, self.disabled_routes, self.disabled_modes = set(), set(), set()
        self.disabled_routes.update(r.id for r in topology.routes.values() if r.status == "DISRUPTED")
        self._events, self._seq = deque(maxlen=500), 0
        self.plan, self.reason, self.computed_at, self.resolves = None, None, None, 0
        self.resolve("startup")

    # ------------------------------------------------------------------ solving
    def resolve(self, reason="manual"):
        with self.lock:
            self.plan = solve_network(self.topology, self.disabled_nodes, self.disabled_routes, self.disabled_modes)
            self.reason, self.computed_at, self.resolves = reason, now_iso(), self.resolves + 1
            return self.solver_block()

    def solver_block(self):
        with self.lock:
            p = self.plan
            return {"status": p["status"], "message": p["message"], "objective_cost": p["objective_cost"],
                    "shipping_cost": p["shipping_cost"], "supply_cost": p["supply_cost"],
                    "shortage_cost": p["shortage_cost"], "solve_ms": p["solve_ms"], "unmet": p["unmet"],
                    "unmet_by_node": p["unmet_by_node"], "fulfillment_pct": p["fulfillment_pct"],
                    "mode_split": p["mode_split"], "variables": p["variables"], "constraints": p["constraints"],
                    "reason": self.reason, "computed_at": self.computed_at, "resolves": self.resolves}

    # ------------------------------------------------------------------ disruptions
    def set_status(self, target, ident, status, reason="manual"):
        """Disrupt or restore a node, route or transport mode, then re-solve. Raises ValueError on bad input."""
        status = str(status).upper()
        if target not in TARGETS:
            raise ValueError(f"target must be one of {', '.join(TARGETS)}")
        if status not in ("DISRUPTED", "ACTIVE"):
            raise ValueError("status must be DISRUPTED or ACTIVE")
        topo = self.topology
        if target == "node" and ident not in topo.nodes:
            raise ValueError(f"unknown node '{ident}'")
        if target == "route" and ident not in topo.routes:
            raise ValueError(f"unknown route '{ident}'")
        if target == "mode" and ident not in MODES:
            raise ValueError(f"unknown mode '{ident}'")
        with self.lock:
            bucket = {"node": self.disabled_nodes, "route": self.disabled_routes, "mode": self.disabled_modes}[target]
            changed = (ident not in bucket) if status == "DISRUPTED" else (ident in bucket)
            if status == "DISRUPTED":
                bucket.add(ident)
            else:
                bucket.discard(ident)
            solver = self.resolve(f"{target} {ident} {status.lower()} ({reason})") if changed else self.solver_block()
            if changed:
                self._log_change(target, ident, status, reason, solver)
            return solver

    def reset(self):
        with self.lock:
            had = bool(self.disabled_nodes or self.disabled_routes or self.disabled_modes)
            self.disabled_nodes.clear()
            self.disabled_modes.clear()
            self.disabled_routes.clear()
            self.disabled_routes.update(r.id for r in self.topology.routes.values() if r.status == "DISRUPTED")
            solver = self.resolve("reset")
            if had:
                self._event("RESET", f"All disruptions cleared, re-solved in {solver['solve_ms']:.0f} ms", "INFO")
                self._persist_route_statuses()
            return solver

    def _log_change(self, target, ident, status, reason, solver):
        label = {"node": "Node", "route": "Route", "mode": "Mode"}[target]
        unmet = sum(solver["unmet"].values())
        text = (f"{label} {ident} {'disrupted' if status == 'DISRUPTED' else 'restored'} ({reason}). "
                f"Re-solved in {solver['solve_ms']:.0f} ms, cost {solver['objective_cost']:,.0f}, "
                f"unmet demand {unmet:,.0f}")
        severity = ("CRITICAL" if unmet > 0 else "WARNING") if status == "DISRUPTED" else "INFO"
        self._event(f"{target.upper()}_{'DISRUPTED' if status == 'DISRUPTED' else 'RESTORED'}", text, severity)
        if target == "route" and self.persist:
            try:
                self.persist.set_route_status(ident, status)
            except Exception as e:
                log.warning("could not persist route status: %s", e)

    def _persist_route_statuses(self):
        if not self.persist:
            return
        for route in self.topology.routes.values():
            try:
                self.persist.set_route_status(route.id, "DISRUPTED" if route.id in self.disabled_routes else "ACTIVE")
            except Exception as e:
                log.warning("could not persist route status: %s", e)
                return

    # ------------------------------------------------------------------ events and state
    def _event(self, event_type, description, severity):
        with self.lock:
            self._seq += 1
            self._events.appendleft({"event_id": self._seq, "type": event_type, "severity": severity,
                                     "description": description, "time": now_iso(), "node": None})
        if self.persist:
            try:
                self.persist.record_event(event_type, description, severity)
            except Exception as e:
                log.warning("could not persist event: %s", e)

    def recent_events(self, limit=50):
        with self.lock:
            return list(self._events)[:limit]

    def snapshot(self):
        """Controller-owned part of /api/state: per-node and per-route plan data."""
        with self.lock:
            plan, topo = self.plan, self.topology
            nodes = {nid: {"disrupted": nid in self.disabled_nodes, "supply_drawn": plan["supply"].get(nid, {}),
                           "unmet": plan["unmet_by_node"].get(nid, {})} for nid in topo.nodes}
            routes = {}
            for r in topo.routes.values():
                disrupted = r.id in self.disabled_routes
                usable = not disrupted and r.src not in self.disabled_nodes and r.dst not in self.disabled_nodes
                routes[r.id] = {"status": "DISRUPTED" if disrupted else "ACTIVE", "usable": usable,
                                "flows": plan["flows"].get(r.id, {})}
            return {"nodes": nodes, "routes": routes, "solver": self.solver_block(),
                    "disabled_modes": sorted(self.disabled_modes)}