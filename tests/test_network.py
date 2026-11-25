"""Tests for network_optimizer.py, network_controller.py and gui_server.py. No database needed.
Run from the project folder:  python -m unittest discover -s tests -v
"""
import json
import os
import random
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gui_server
import topology_loader as tl
from network_controller import NetworkController
from network_optimizer import solve_network

DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "demo_topology.json")


def mode(cost, days, cap):
    return {"cost_per_unit": cost, "transit_days": days, "capacity_per_day": cap}


def tiny(**overrides):
    """S -> H -> A chain with one resource. Sea is cheap but slow, air is dear but fast."""
    d = {
        "resources": [{"id": "X"}],
        "nodes": [
            {"id": "S", "role": "SUPPLIER", "lat": 0, "lon": 0, "supply": {"X": {"capacity_per_day": 100, "unit_cost": 10}}},
            {"id": "H", "role": "HUB", "lat": 1, "lon": 1},
            {"id": "A", "role": "ASSEMBLY", "lat": 2, "lon": 2, "demand": {"X": 60}},
        ],
        "routes": [
            {"id": "SH", "from": "S", "to": "H", "modes": {"sea": mode(1, 5, 1000), "air": mode(5, 1, 1000)}},
            {"id": "HA", "from": "H", "to": "A", "modes": {"sea": mode(1, 5, 1000), "air": mode(5, 1, 1000)}},
        ],
        "solver": {"shortage_penalty": 1000, "time_value_per_day": 0},
    }
    d.update(overrides)
    return tl.parse_topology(d)


class OptimizerTests(unittest.TestCase):
    def test_cheapest_mode_chosen(self):
        p = solve_network(tiny())
        self.assertEqual(p["status"], "OPTIMAL")
        self.assertEqual(p["flows"]["SH"], {"sea": {"X": 60.0}})
        self.assertEqual(p["objective_cost"], 60 * 10 + 60 * 1 + 60 * 1)
        self.assertEqual(p["unmet"], {})
        self.assertEqual(p["fulfillment_pct"], 100.0)

    def test_time_value_switches_to_air(self):
        t = tiny(solver={"shortage_penalty": 1000, "time_value_per_day": 2})   # sea 1+10=11, air 5+2=7
        self.assertIn("air", solve_network(t)["flows"]["SH"])

    def test_capacity_forces_modal_split(self):
        d = tiny()
        d.routes["SH"].modes["sea"] = tl.Mode("sea", 1, 5, 40)                 # only 40 fit on sea
        p = solve_network(d)
        self.assertEqual(p["flows"]["SH"], {"sea": {"X": 40.0}, "air": {"X": 20.0}})
        self.assertEqual(p["unmet"], {})

    def test_disabled_mode_and_route(self):
        t = tiny()
        self.assertEqual(list(solve_network(t, disabled_modes={"sea"})["flows"]["SH"]), ["air"])
        p = solve_network(t, disabled_routes={"HA"})
        self.assertEqual(p["unmet"], {"X": 60.0})
        self.assertEqual(p["shortage_cost"], 60 * 1000)

    def test_disabled_supplier_node_leaves_demand_unmet(self):
        p = solve_network(tiny(), disabled_nodes={"S"})
        self.assertEqual(p["unmet"], {"X": 60.0})
        self.assertEqual(p["flows"], {})

    def test_disabled_demand_node_counts_as_unmet(self):
        p = solve_network(tiny(), disabled_nodes={"A"})
        self.assertEqual(p["unmet"], {"X": 60.0})

    def test_supply_capacity_limits_flow(self):
        t = tiny()
        t.nodes["A"].demand["X"] = 150
        p = solve_network(t)
        self.assertEqual(p["supply"]["S"]["X"], 100.0)
        self.assertEqual(p["unmet"], {"X": 50.0})

    def test_resources_share_arc_capacity(self):
        d = {
            "resources": [{"id": "X"}, {"id": "Y"}],
            "nodes": [
                {"id": "S", "role": "SUPPLIER", "lat": 0, "lon": 0,
                 "supply": {"X": {"capacity_per_day": 100, "unit_cost": 1}, "Y": {"capacity_per_day": 100, "unit_cost": 1}}},
                {"id": "A", "role": "ASSEMBLY", "lat": 1, "lon": 1, "demand": {"X": 50, "Y": 50}},
            ],
            "routes": [{"id": "SA", "from": "S", "to": "A", "modes": {"sea": mode(1, 1, 70)}}],
            "solver": {"shortage_penalty": 500},
        }
        p = solve_network(tl.parse_topology(d))
        self.assertAlmostEqual(sum(p["flows"]["SA"]["sea"].values()), 70.0)    # shared 70, not 100
        self.assertAlmostEqual(sum(p["unmet"].values()), 30.0)

    def test_demo_baseline_and_disruption_cascade(self):
        t = tl.load_topology(DEMO)
        base = solve_network(t)
        self.assertEqual(base["unmet"], {})
        self.assertEqual(base["fulfillment_pct"], 100.0)
        one = solve_network(t, disabled_routes={"R-TW-SH"})
        self.assertEqual(one["unmet"], {})                                     # rerouted via Hong Kong
        self.assertGreater(one["objective_cost"], base["objective_cost"] - 1)
        both = solve_network(t, disabled_routes={"R-TW-SH", "R-TW-HK"})
        self.assertEqual(both["unmet"], {"CHIPS": 2000.0})                     # Japan backup is only 2500 of 4500
        self.assertGreater(both["objective_cost"], 1_000_000)
        no_sea = solve_network(t, disabled_modes={"sea"})
        self.assertEqual(no_sea["unmet"], {"BATTERIES": 1500.0})               # air cannot carry the battery volume

    def test_flow_conservation_in_demo_plan(self):
        t = tl.load_topology(DEMO)
        p = solve_network(t)
        for res in t.resources:
            net = {}
            for rid, per_mode in p["flows"].items():
                for m_flows in per_mode.values():
                    v = m_flows.get(res, 0)
                    net[t.routes[rid].src] = net.get(t.routes[rid].src, 0) - v
                    net[t.routes[rid].dst] = net.get(t.routes[rid].dst, 0) + v
            for nid, node in t.nodes.items():
                drawn = p["supply"].get(nid, {}).get(res, 0)
                self.assertAlmostEqual(net.get(nid, 0) + drawn, node.demand.get(res, 0), places=1)

    def test_scales_to_a_large_network(self):
        rnd = random.Random(7)
        n_res, nodes, routes = 6, [], []
        res = [{"id": f"R{i}"} for i in range(n_res)]
        for i in range(60):
            nodes.append({"id": f"S{i}", "role": "SUPPLIER", "lat": rnd.uniform(-50, 50), "lon": rnd.uniform(-170, 170),
                          "supply": {f"R{rnd.randrange(n_res)}": {"capacity_per_day": rnd.randint(500, 5000),
                                                                  "unit_cost": rnd.uniform(5, 30)}}})
        for i in range(40):
            nodes.append({"id": f"H{i}", "role": "HUB", "lat": 0, "lon": 0})
        for i in range(100):
            nodes.append({"id": f"A{i}", "role": "ASSEMBLY", "lat": 0, "lon": 0,
                          "demand": {f"R{rnd.randrange(n_res)}": rnd.randint(50, 500)}})
        ids = [n["id"] for n in nodes]
        for i in range(700):
            a, b = rnd.sample(ids, 2)
            routes.append({"id": f"E{i}", "from": a, "to": b,
                           "modes": {"sea": mode(rnd.uniform(.2, 1), rnd.randint(2, 20), rnd.randint(500, 5000)),
                                     "air": mode(rnd.uniform(2, 6), rnd.randint(1, 3), rnd.randint(100, 1000))}})
        t = tl.parse_topology({"resources": res, "nodes": nodes, "routes": routes,
                               "solver": {"shortage_penalty": 500, "time_value_per_day": 0.5}})
        p = solve_network(t)
        print(f"\n[scale] {len(t.nodes)} nodes, {len(t.routes)} routes, {p['variables']} variables, "
              f"{p['constraints']} constraints: solved in {p['solve_ms']:.0f} ms")
        self.assertEqual(p["status"], "OPTIMAL")
        self.assertLess(p["solve_ms"], 10_000)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.saved = []

        class Persist:
            def __init__(inner):
                inner.events, inner.routes = [], []

            def record_event(inner, *a):
                inner.events.append(a)

            def set_route_status(inner, *a):
                inner.routes.append(a)
        self.persist = Persist()
        self.c = NetworkController(tl.load_topology(DEMO), persist=self.persist)

    def test_disrupt_restore_cycle(self):
        base = self.c.solver_block()["objective_cost"]
        s = self.c.set_status("node", "SUP-TW", "DISRUPTED")
        self.assertEqual(s["unmet"], {"CHIPS": 2000.0})
        self.assertIn("node SUP-TW disrupted", s["reason"])
        self.assertTrue(self.c.snapshot()["nodes"]["SUP-TW"]["disrupted"])
        self.assertEqual(self.c.set_status("node", "SUP-TW", "ACTIVE")["objective_cost"], base)

    def test_events_and_persistence(self):
        self.c.set_status("route", "R-TW-SH", "DISRUPTED")
        self.c.set_status("route", "R-TW-HK", "DISRUPTED")
        events = self.c.recent_events()
        self.assertEqual(events[0]["severity"], "CRITICAL")                     # second disruption causes a shortage
        self.assertEqual(events[1]["severity"], "WARNING")
        self.assertEqual(self.persist.routes, [("R-TW-SH", "DISRUPTED"), ("R-TW-HK", "DISRUPTED")])
        self.assertEqual(len(self.persist.events), 2)

    def test_repeating_a_change_is_a_no_op(self):
        self.c.set_status("route", "R-TW-SH", "DISRUPTED")
        resolves = self.c.resolves
        self.c.set_status("route", "R-TW-SH", "DISRUPTED")
        self.assertEqual(self.c.resolves, resolves)

    def test_bad_input_rejected(self):
        for args in (("planet", "x", "DISRUPTED"), ("node", "NOPE", "DISRUPTED"), ("route", "NOPE", "ACTIVE"),
                     ("mode", "teleport", "DISRUPTED"), ("node", "SUP-TW", "BROKEN")):
            with self.assertRaises(ValueError):
                self.c.set_status(*args)

    def test_reset_and_json_status(self):
        self.c.set_status("route", "R-TW-SH", "DISRUPTED")
        self.c.set_status("mode", "air", "DISRUPTED")
        self.c.reset()
        snap = self.c.snapshot()
        self.assertTrue(all(r["status"] == "ACTIVE" for r in snap["routes"].values()))
        self.assertEqual(snap["disabled_modes"], [])

    def test_route_marked_disrupted_in_the_file_starts_disabled(self):
        with open(DEMO, encoding="utf-8") as fh:
            d = json.load(fh)
        d["routes"][0]["status"] = "DISRUPTED"
        c = NetworkController(tl.parse_topology(d))
        self.assertEqual(c.snapshot()["routes"][d["routes"][0]["id"]]["status"], "DISRUPTED")

    def test_persistence_failure_never_breaks_the_plan(self):
        class Broken:
            def record_event(self, *a):
                raise RuntimeError("db down")

            def set_route_status(self, *a):
                raise RuntimeError("db down")
        c = NetworkController(tl.load_topology(DEMO), persist=Broken())
        self.assertEqual(c.set_status("route", "R-TW-SH", "DISRUPTED")["status"], "OPTIMAL")

    def test_snapshot_marks_unusable_routes(self):
        self.c.set_status("node", "HUB-SH", "DISRUPTED")
        routes = self.c.snapshot()["routes"]
        self.assertFalse(routes["R-TW-SH"]["usable"])
        self.assertEqual(routes["R-TW-SH"]["status"], "ACTIVE")


class FakeDb:
    """Stands in for DbReader to test the live-status logic."""
    def __init__(self):
        self.status, self.ok = {}, True

    def live_snapshot(self, ttl=1.0):
        return {"nodes": {k: {"status": v, "heartbeat_age_s": 1, "memory_pct": 2.5, "active_jobs": 0,
                              "inventory": {"CHIPS": 99.0}} for k, v in self.status.items()},
                "central": {"scheduler": {"policy": "PRIORITY", "queue_depth": 3, "workers": 2,
                                          "aging_interval_s": 15, "dispatched": 7},
                            "database": {"transactions": 10, "deadlock_retries": 1, "failures": 0,
                                         "avg_tx_ms": 4.2, "write_slots": 4}}}

    def events(self, limit):
        return []

    def record_event(self, *a):
        pass

    def set_route_status(self, *a):
        pass


class LiveSyncTests(unittest.TestCase):
    def test_lost_heartbeat_reroutes_then_restores(self):
        db = FakeDb()
        c = NetworkController(tl.load_topology(DEMO))
        sync = gui_server.LiveSync(c, db)
        db.status = {n: "OFFLINE" for n in c.topology.nodes}               # never seen online: ignored
        sync.tick()
        self.assertEqual(c.disabled_nodes, set())
        db.status = {n: "ONLINE" for n in c.topology.nodes}
        sync.tick()
        db.status["SUP-TW"] = "OFFLINE"                                     # was online, now lost
        sync.tick()
        self.assertEqual(c.disabled_nodes, {"SUP-TW"})
        self.assertEqual(c.solver_block()["unmet"], {"CHIPS": 2000.0})
        db.status["SUP-TW"] = "ONLINE"
        sync.tick()
        self.assertEqual(c.disabled_nodes, set())

    def test_manual_disruption_is_not_auto_restored(self):
        db = FakeDb()
        c = NetworkController(tl.load_topology(DEMO))
        sync = gui_server.LiveSync(c, db)
        db.status = {n: "ONLINE" for n in c.topology.nodes}
        sync.tick()
        c.set_status("node", "SUP-KR", "DISRUPTED")
        sync.tick()
        self.assertEqual(c.disabled_nodes, {"SUP-KR"})


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = tempfile.TemporaryDirectory()
        with open(os.path.join(cls.web.name, "index.html"), "w") as fh:
            fh.write("<h1>ui</h1>")
        cls.db = FakeDb()
        cls.db.status = {n: "ONLINE" for n in tl.load_topology(DEMO).nodes}
        cls.controller = NetworkController(tl.load_topology(DEMO))
        cls.server = gui_server.create_server(gui_server.App(cls.controller, cls.db, cls.web.name), "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.web.cleanup()

    def setUp(self):
        self.controller.reset()

    def call(self, path, body=None, method=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get_json(self, path):
        status, body = self.call(path)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_health_and_topology(self):
        self.assertTrue(self.get_json("/api/health")["ok"])
        topo = self.get_json("/api/topology")
        self.assertEqual((len(topo["nodes"]), len(topo["routes"])), (9, 13))
        self.assertEqual(topo["routes"][0]["modes"]["sea"]["capacity_per_day"], 12000.0)

    def test_state_matches_the_contract(self):
        s = self.get_json("/api/state")
        for key in ("time", "nodes", "routes", "scheduler", "database", "solver", "system"):
            self.assertIn(key, s)
        node = next(n for n in s["nodes"] if n["id"] == "SUP-TW")
        for key in ("status", "disrupted", "heartbeat_age_s", "memory_pct", "cpu_pct", "active_jobs", "inventory"):
            self.assertIn(key, node)
        self.assertEqual((node["status"], node["inventory"], node["inventory_source"]),
                         ("ONLINE", {"CHIPS": 99.0}, "database"))
        self.assertEqual(s["scheduler"]["queue_depth"], 3)
        self.assertEqual(s["database"]["deadlock_retries"], 1)
        self.assertEqual(s["solver"]["status"], "OPTIMAL")
        self.assertEqual(s["system"]["nodes_online"], 9)
        route = next(r for r in s["routes"] if r["id"] == "R-TW-SH")
        self.assertIn("flows", route)

    def test_disrupt_replans_and_logs(self):
        status, body = self.call("/api/disrupt", {"target": "node", "id": "SUP-TW", "status": "DISRUPTED"})
        out = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(out["applied"])
        self.assertEqual(out["solver"]["unmet"], {"CHIPS": 2000.0})
        self.assertLess(out["solver"]["solve_ms"], 1000)
        state = self.get_json("/api/state")
        self.assertTrue(next(n for n in state["nodes"] if n["id"] == "SUP-TW")["disrupted"])
        self.assertEqual(self.get_json("/api/events")[0]["severity"], "CRITICAL")
        status, body = self.call("/api/reset", {})
        self.assertEqual(json.loads(body)["solver"]["unmet"], {})

    def test_errors(self):
        self.assertEqual(self.call("/api/disrupt", {"target": "node", "id": "NOPE"})[0], 400)
        self.assertEqual(self.call("/api/disrupt", b"not json")[0], 400)
        self.assertEqual(self.call("/api/disrupt", b"[1]")[0], 400)
        self.assertEqual(self.call("/api/nothing")[0], 404)
        self.assertEqual(self.call("/api/policy", {"scheduling": "FCFS"})[0], 501)
        self.assertEqual(self.call("/api/events?limit=abc")[0], 400)
        self.assertEqual(self.call("/api/disrupt", b"x" * 70_000)[0], 413)

    def test_static_files_and_path_traversal(self):
        status, body = self.call("/")
        self.assertEqual((status, body), (200, b"<h1>ui</h1>"))
        self.assertEqual(self.call("/../gui_server.py")[0], 403)
        self.assertEqual(self.call("/%2e%2e/gui_server.py")[0], 403)
        self.assertEqual(self.call("/missing.js")[0], 404)

    def test_without_database_fields_are_null_not_errors(self):
        app = gui_server.App(NetworkController(tl.load_topology(DEMO)), None, self.web.name)
        s = app.state()
        node = next(n for n in s["nodes"] if n["id"] == "SUP-TW")
        self.assertEqual((node["status"], node["memory_pct"], node["inventory_source"]), ("UNKNOWN", None, "topology"))
        self.assertEqual(node["inventory"], {"CHIPS": 30000.0})
        self.assertIsNone(s["scheduler"]["queue_depth"])
        self.assertFalse(s["database_connected"])
        self.assertEqual(len(app.events(10)), 0)


if __name__ == "__main__":
    unittest.main()