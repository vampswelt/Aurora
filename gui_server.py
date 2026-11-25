"""
gui_server.py : HTTP bridge between the browser GUI and the backend. Standard library only.

  python gui_server.py                                  # http://127.0.0.1:8080
  python gui_server.py --topology config/apple_global_topology.json --port 8080
  python gui_server.py --no-db                          # topology + solver only, no MySQL

Serves the UI files from web/ and the JSON API described in the GUI handoff brief:
  GET  /api/health /api/topology /api/state /api/solver
  GET  /api/processes /api/allocations /api/events /api/jobs        (?limit=N)
  POST /api/disrupt   {"target": "route"|"node"|"mode", "id": "...", "status": "DISRUPTED"|"ACTIVE"}
  POST /api/reset     clears every disruption
  POST /api/policy    501 until Central exposes an admin channel

Topology, flows, solver and disruptions come from NetworkController (works with no database).
Node status, inventory, processes, allocations and events come from MySQL when it is reachable,
otherwise the fields are null/empty and the UI shows "no data". If a node that was ONLINE stops
sending heartbeats, it is disrupted automatically and the network is re-solved.
Binds to 127.0.0.1 by default and has no authentication: do not expose it on an open network.
"""
import argparse
import json
import logging
import mimetypes
import os
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import psutil

from network_controller import NetworkController
from topology_loader import TopologyError, describe, load_topology

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("gui_server")
MAX_BODY = 64 * 1024
PLACEHOLDER = (b"<!doctype html><meta charset=utf-8><title>Command Center API</title>"
               b"<body style='font:15px monospace;background:#0b0f14;color:#cfd8e3;padding:2rem'>"
               b"<h2>Backend is running</h2><p>No UI found in <code>web/</code>. Put index.html there.</p>"
               b"<ul><li><a href=/api/health>/api/health</a></li><li><a href=/api/topology>/api/topology</a></li>"
               b"<li><a href=/api/state>/api/state</a></li><li><a href=/api/events>/api/events</a></li></ul>")


def json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return str(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------------------- database access (optional)
class DbReader:
    """Reads live data from MySQL and persists disruptions. Every call degrades gracefully."""
    def __init__(self, connect=None):
        self._connect = connect or self._default_connect
        self.ok = False
        self._cache, self._cache_at, self._lock = None, 0.0, threading.Lock()

    @staticmethod
    def _default_connect():
        import pymysql
        from pymysql.cursors import DictCursor
        return pymysql.connect(host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3306")),
                               user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""),
                               database=os.getenv("DB_NAME", "ogas_command"), cursorclass=DictCursor,
                               autocommit=True, connect_timeout=3)

    def _run(self, sql, params=(), fetch=True):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall() if fetch else None
            self.ok = True
            return rows
        except Exception:
            self.ok = False
            raise
        finally:
            conn.close()

    def ping(self):
        try:
            self._run("SELECT 1")
            return True
        except Exception as e:
            log.warning("database not reachable: %s", e)
            return False

    def live_snapshot(self, ttl=1.0):
        """{'nodes': {name: {...}}, 'central': dict|None} or None when the database is unavailable."""
        with self._lock:
            if self._cache is not None and time.time() - self._cache_at < ttl:
                return self._cache
        try:
            nodes = {r["node_name"]: {"status": r["status"], "heartbeat_age_s": r["age"], "memory_pct": None,
                                      "active_jobs": None, "inventory": {}}
                     for r in self._run("SELECT node_name, status, TIMESTAMPDIFF(SECOND, last_heartbeat, NOW()) "
                                        "AS age FROM Node")}
            central = None
            for r in self._run(
                    "SELECT n.node_name, s.active_processes, s.memory_usage, s.status_details FROM System_Status s "
                    "JOIN (SELECT node_id, MAX(status_id) AS mid FROM System_Status "
                    "WHERE snapshot_time > NOW() - INTERVAL 2 MINUTE GROUP BY node_id) m ON m.mid = s.status_id "
                    "JOIN Node n ON n.node_id = s.node_id"):
                if r["node_name"] == "CENTRAL":
                    try:
                        central = json.loads(r["status_details"] or "{}")
                    except ValueError:
                        central = None
                elif r["node_name"] in nodes:
                    nodes[r["node_name"]]["memory_pct"] = r["memory_usage"]
                    nodes[r["node_name"]]["active_jobs"] = r["active_processes"]
            for r in self._run("SELECT n.node_name, rs.resource_name, i.available_quantity FROM Resource_Inventory i "
                               "JOIN Node n ON n.node_id = i.node_id JOIN Resource rs ON rs.resource_id = i.resource_id"):
                if r["node_name"] in nodes:
                    nodes[r["node_name"]]["inventory"][r["resource_name"]] = r["available_quantity"]
            snap = {"nodes": nodes, "central": central}
        except Exception as e:
            log.debug("live snapshot failed: %s", e)
            snap = None
        with self._lock:
            self._cache, self._cache_at = snap, time.time()
        return snap

    def processes(self, limit):
        return self._run("SELECT p.process_id, p.process_name AS name, n.node_name AS node, p.priority, p.status, "
                         "p.created_at, (SELECT COUNT(*) FROM Request rq WHERE rq.process_id = p.process_id) AS "
                         "`lines` FROM `Process` p JOIN Node n ON n.node_id = p.node_id "
                         "ORDER BY p.process_id DESC LIMIT %s", (limit,))

    def allocations(self, limit):
        return self._run("SELECT a.allocation_id, a.request_id, rs.resource_name AS resource, sn.node_name AS "
                         "supplier, a.quantity_allocated AS quantity, a.status, a.allocated_at FROM Allocation a "
                         "JOIN Resource rs ON rs.resource_id = a.resource_id JOIN Node sn ON sn.node_id = a.node_id "
                         "ORDER BY a.allocation_id DESC LIMIT %s", (limit,))

    def events(self, limit):
        return self._run("SELECT e.event_id, e.event_type AS type, e.severity, e.event_description AS description, "
                         "e.event_time AS time, n.node_name AS node FROM System_Event e "
                         "LEFT JOIN Node n ON n.node_id = e.node_id ORDER BY e.event_id DESC LIMIT %s", (limit,))

    # persistence hooks used by NetworkController
    def record_event(self, event_type, description, severity):
        self._run("INSERT INTO System_Event (event_type, event_description, severity) VALUES (%s,%s,%s)",
                  (event_type[:48], description[:255], severity), fetch=False)

    def set_route_status(self, code, status):
        self._run("UPDATE Route SET status=%s WHERE code=%s", (status, code), fetch=False)


class LiveSync(threading.Thread):
    """Disrupts a node when a previously ONLINE node goes OFFLINE (heartbeats lost), restores it on return."""
    def __init__(self, controller, db, interval=2.0):
        super().__init__(daemon=True, name="live-sync")
        self.controller, self.db, self.interval = controller, db, interval
        self.seen, self.auto = set(), set()
        self._stop_event = threading.Event()

    def tick(self):
        live = self.db.live_snapshot(ttl=0)
        if not live:
            return
        for node_id in self.controller.topology.nodes:
            status = live["nodes"].get(node_id, {}).get("status")
            if status == "ONLINE":
                self.seen.add(node_id)
                if node_id in self.auto:
                    self.auto.discard(node_id)
                    self.controller.set_status("node", node_id, "ACTIVE", reason="heartbeat restored")
            elif status == "OFFLINE" and node_id in self.seen and node_id not in self.auto \
                    and node_id not in self.controller.disabled_nodes:
                self.auto.add(node_id)
                self.controller.set_status("node", node_id, "DISRUPTED", reason="heartbeat lost")

    def run(self):
        while not self._stop_event.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("live sync failed")

    def stop(self):
        self._stop_event.set()


# ---------------------------------------------------------------------------- application
class App:
    def __init__(self, controller, db=None, web_dir=None):
        self.controller, self.db = controller, db
        self.web_dir = os.path.realpath(web_dir or os.path.join(BASE_DIR, "web"))
        psutil.cpu_percent(None)                      # prime the non-blocking CPU reading

    def state(self):
        snap = self.controller.snapshot()
        live = self.db.live_snapshot() if self.db else None
        topo_nodes = self.controller.topology.nodes
        nodes, online = [], 0
        for nid, node in topo_nodes.items():
            mine = (live or {}).get("nodes", {}).get(nid)
            status = mine["status"] if mine else "UNKNOWN"
            online += status == "ONLINE"
            from_db = bool(mine and mine["inventory"])
            nodes.append({"id": nid, "status": status, "disrupted": snap["nodes"][nid]["disrupted"],
                          "heartbeat_age_s": mine["heartbeat_age_s"] if mine else None,
                          "memory_pct": mine["memory_pct"] if mine else None, "cpu_pct": None,
                          "active_jobs": mine["active_jobs"] if mine else None,
                          "inventory": mine["inventory"] if from_db else node.stock,
                          "inventory_source": "database" if from_db else "topology",
                          "supply_drawn": snap["nodes"][nid]["supply_drawn"], "unmet": snap["nodes"][nid]["unmet"]})
        central = (live or {}).get("central") or {}
        sched, dbs = central.get("scheduler") or {}, central.get("database") or {}
        return {
            "time": datetime.now().isoformat(timespec="seconds"),
            "database_connected": bool(self.db and self.db.ok),
            "nodes": nodes,
            "routes": [{"id": rid, **info} for rid, info in snap["routes"].items()],
            "disabled_modes": snap["disabled_modes"],
            "scheduler": {k: sched.get(k) for k in ("policy", "queue_depth", "workers", "aging_interval_s",
                                                    "dispatched")},
            "database": {k: dbs.get(k) for k in ("transactions", "deadlock_retries", "failures", "avg_tx_ms",
                                                 "write_slots")},
            "solver": snap["solver"],
            "system": {"cpu_pct": psutil.cpu_percent(None), "mem_pct": psutil.virtual_memory().percent,
                       "threads": threading.active_count(), "nodes_online": online},
        }

    def db_list(self, kind, limit):
        if not self.db:
            return None
        try:
            return getattr(self.db, kind)(limit)
        except Exception as e:
            log.debug("%s query failed: %s", kind, e)
            return None

    def events(self, limit):
        rows = self.db_list("events", limit)
        return rows if rows else self.controller.recent_events(limit)

    def disrupt(self, body):
        target, ident = body.get("target"), body.get("id")
        status = body.get("status", "DISRUPTED")
        try:
            solver = self.controller.set_status(target, ident, status)
        except ValueError as e:
            raise ApiError(400, str(e))
        return {"applied": True, "solver": solver}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CommandCenter/1.0"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status, body, ctype="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, default=json_default).encode("utf-8"))

        def _limit(self, query, default=50):
            try:
                return max(1, min(500, int(query.get("limit", [default])[0])))
            except ValueError:
                raise ApiError(400, "limit must be an integer")

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, "request body too large")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise ApiError(400, "body must be valid JSON")
            if not isinstance(data, dict):
                raise ApiError(400, "body must be a JSON object")
            return data

        def do_GET(self):
            url = urlparse(self.path)
            try:
                if not url.path.startswith("/api/"):
                    return self._static(url.path)
                q, c = parse_qs(url.query), app.controller
                if url.path == "/api/health":
                    topo = c.topology
                    return self._json(200, {"ok": True, "database_connected": bool(app.db and app.db.ok),
                                            "topology": topo.meta["name"], "fingerprint": topo.fingerprint})
                if url.path == "/api/topology":
                    return self._json(200, c.topology.public_dict())
                if url.path == "/api/state":
                    return self._json(200, app.state())
                if url.path == "/api/solver":
                    return self._json(200, c.solver_block())
                if url.path == "/api/events":
                    return self._json(200, app.events(self._limit(q)))
                if url.path in ("/api/processes", "/api/allocations"):
                    rows = app.db_list(url.path[5:], self._limit(q))
                    return self._json(200, rows or [])
                if url.path == "/api/jobs":
                    return self._json(200, [])                  # planned: node job history
                raise ApiError(404, "unknown endpoint")
            except ApiError as e:
                self._json(e.status, {"error": e.message})
            except Exception as e:
                log.exception("GET %s failed", self.path)
                self._json(500, {"error": f"internal error: {type(e).__name__}"})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if path == "/api/disrupt":
                    return self._json(200, app.disrupt(self._body()))
                if path == "/api/reset":
                    self._body()
                    return self._json(200, {"applied": True, "solver": app.controller.reset()})
                if path == "/api/policy":
                    raise ApiError(501, "switching the scheduling policy needs Central's admin channel (planned)")
                raise ApiError(404, "unknown endpoint")
            except ApiError as e:
                self._json(e.status, {"error": e.message})
            except Exception as e:
                log.exception("POST %s failed", self.path)
                self._json(500, {"error": f"internal error: {type(e).__name__}"})

        def _static(self, path):
            rel = "index.html" if path in ("", "/") else unquote(path).lstrip("/")
            if "\x00" in rel:
                raise ApiError(400, "bad path")
            full = os.path.realpath(os.path.join(app.web_dir, rel))
            if full != app.web_dir and not full.startswith(app.web_dir + os.sep):
                raise ApiError(403, "forbidden")
            if os.path.isfile(full):
                with open(full, "rb") as fh:
                    return self._send(200, fh.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")
            if rel == "index.html":
                return self._send(200, PLACEHOLDER, "text/html; charset=utf-8")
            raise ApiError(404, "not found")

    return Handler


def create_server(app, host="127.0.0.1", port=8080):
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server


def main():
    ap = argparse.ArgumentParser(description="Command Center GUI server")
    ap.add_argument("--topology", help="topology JSON (default: TOPOLOGY_PATH or config/demo_topology.json)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--web", default=os.path.join(BASE_DIR, "web"), help="folder with the UI files")
    ap.add_argument("--no-db", action="store_true", help="run without MySQL")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        topo = load_topology(args.topology)
    except TopologyError as e:
        print(e)
        raise SystemExit(1)
    print(describe(topo))
    db = None if args.no_db else DbReader()
    if db and not db.ping():
        print("MySQL not reachable: live node status, processes and events will be empty until it is.")
    controller = NetworkController(topo, persist=db)
    if db:
        LiveSync(controller, db).start()
    solver = controller.solver_block()
    print(f"Initial plan: cost {solver['objective_cost']:,.0f}, fulfilment {solver['fulfillment_pct']}%, "
          f"solved in {solver['solve_ms']:.0f} ms")
    server = create_server(App(controller, db, args.web), args.host, args.port)
    print(f"Command Center at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()