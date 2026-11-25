"""Tests for topology_loader.py and topology_seed.py. No database needed.
Run from the project folder:  python -m unittest discover -s tests -v
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import topology_loader as tl
import topology_seed as ts

DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "demo_topology.json")


def demo_data():
    with open(DEMO, encoding="utf-8") as fh:
        return json.load(fh)


def errors_for(data):
    try:
        tl.parse_topology(data)
    except tl.TopologyError as e:
        return e.errors
    return []


class LoaderTests(unittest.TestCase):
    def test_demo_loads_cleanly(self):
        topo = tl.load_topology(DEMO)
        self.assertEqual(len(topo.resources), 3)
        self.assertEqual(len(topo.nodes), 9)
        self.assertEqual(len(topo.routes), 13)
        self.assertEqual(topo.warnings, [])
        self.assertEqual(topo.nodes["SUP-TW"].supply["CHIPS"]["capacity_per_day"], 6000.0)
        self.assertEqual(set(topo.routes["R-SH-ZZ"].modes), {"air", "rail"})

    def test_public_dict_is_json_safe(self):
        out = json.dumps(tl.load_topology(DEMO).public_dict())
        self.assertIn("SUP-TW", out)

    def test_all_problems_reported_at_once(self):
        d = demo_data()
        d["nodes"][0]["lat"] = 123
        d["nodes"][1]["supply"]["GOLD"] = {"capacity_per_day": 1, "unit_cost": 1}
        d["routes"][0]["to"] = "NOPE"
        d["routes"][1]["modes"]["sea"]["cost_per_unit"] = -1
        errs = "\n".join(errors_for(d))
        for fragment in ("lat must be at most 90", "unknown resource 'GOLD'", "unknown node 'NOPE'",
                         "cost_per_unit must be at least 0"):
            self.assertIn(fragment, errs)

    def test_duplicates_and_bad_values(self):
        d = demo_data()
        d["nodes"].append(copy.deepcopy(d["nodes"][0]))
        d["routes"][2]["from"] = d["routes"][2]["to"]
        d["routes"][3]["modes"] = {}
        d["routes"][4]["status"] = "BROKEN"
        d["routes"][5]["modes"]["teleport"] = {"cost_per_unit": 1, "transit_days": 1, "capacity_per_day": 1}
        d["routes"][6]["modes"]["air"]["transit_days"] = 0
        d["nodes"][3]["role"] = "WIZARD"
        errs = "\n".join(errors_for(d))
        for fragment in ("duplicate node id", "'from' and 'to' must differ", "modes must be a non-empty",
                         "status must be one of", "unknown mode 'teleport'", "transit_days must be greater than 0",
                         "role must be one of"):
            self.assertIn(fragment, errs)

    def test_structural_errors(self):
        self.assertTrue(errors_for([]))
        self.assertIn("'routes' must be a non-empty list", "\n".join(errors_for({**demo_data(), "routes": []})))
        self.assertTrue(errors_for({**demo_data(), "resources": [{"id": "bad id"}]}))

    def test_booleans_and_nan_are_not_numbers(self):
        d = demo_data()
        d["nodes"][0]["lat"] = True
        self.assertIn("lat must be a number", "\n".join(errors_for(d)))

    def test_warnings(self):
        d = demo_data()
        d["nodes"].append({"id": "LONELY", "role": "HUB", "lat": 0, "lon": 0})
        d["resources"].append({"id": "GLASS"})
        d["nodes"][-1]["demand"] = {"GLASS": 5}
        d["nodes"].append({"id": "ASM-NEW", "role": "ASSEMBLY", "lat": 1, "lon": 1, "demand": {"CHIPS": 10}})
        d["nodes"][0]["colour"] = "red"
        topo = tl.parse_topology(d)
        text = "\n".join(topo.warnings)
        self.assertIn("'LONELY' has no routes", text)
        self.assertIn("'GLASS' is demanded but no node supplies it", text)
        self.assertIn("'ASM-NEW' demands 'CHIPS' but no supplier can reach it", text)
        self.assertIn("unknown field 'colour' ignored", text)

    def test_invalid_json_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w") as fh:
                fh.write("{not json")
            with self.assertRaises(tl.TopologyError) as ctx:
                tl.load_topology(bad)
            self.assertIn("not valid JSON", str(ctx.exception))
            with self.assertRaises(tl.TopologyError) as ctx:
                tl.load_topology(os.path.join(tmp, "missing.json"))
            self.assertIn("not found", str(ctx.exception))

    def test_swapping_dataset_needs_only_a_path(self):
        d = demo_data()
        d["meta"]["name"] = "Swapped dataset"
        d["nodes"].append({"id": "SUP-NEW", "role": "SUPPLIER", "lat": 1, "lon": 2,
                           "supply": {"CHIPS": {"capacity_per_day": 10, "unit_cost": 1}}})
        d["routes"].append({"id": "R-NEW", "from": "SUP-NEW", "to": "HUB-SH",
                            "modes": {"sea": {"cost_per_unit": 1, "transit_days": 1, "capacity_per_day": 10}}})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "other.json")
            with open(path, "w") as fh:
                json.dump(d, fh)
            old = os.environ.get(tl.ENV_VAR)
            os.environ[tl.ENV_VAR] = path
            try:
                topo = tl.load_topology()
            finally:
                if old is None:
                    del os.environ[tl.ENV_VAR]
                else:
                    os.environ[tl.ENV_VAR] = old
        self.assertEqual(topo.meta["name"], "Swapped dataset")
        self.assertEqual(len(topo.nodes), 10)
        self.assertNotEqual(topo.fingerprint, tl.load_topology(DEMO).fingerprint)


class FakeCursor:
    """Records statements. Mimics just enough of MySQL for apply_topology."""
    def __init__(self):
        self.calls, self.lastrowid, self._ids, self._last_sql, self._last_params = [], 0, {}, "", ()

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        self._last_sql, self._last_params = sql, tuple(params)
        if sql.startswith("INSERT INTO Node ") or sql.startswith("INSERT INTO Route "):
            key = (sql[:20], params[0])
            self.lastrowid = self._ids.setdefault(key, len(self._ids) + 1)

    def fetchall(self):
        if "FROM Resource" in self._last_sql:
            names = [p[0] for s, p in self.calls if s.startswith("INSERT INTO Resource (")]
            return [{"resource_id": i + 1, "resource_name": n} for i, n in enumerate(names)]
        return []

    def fetchone(self):
        return {"factory_id": 7}


class FakeDb:
    def __init__(self):
        self.cursors = []

    def run_transaction(self, fn, write=True, max_retries=5):
        cur = FakeCursor()
        self.cursors.append(cur)
        return fn(cur)


class SeederTests(unittest.TestCase):
    def setUp(self):
        self.topo = tl.load_topology(DEMO)
        self.cur = FakeCursor()
        self.summary = ts.apply_topology(self.cur, self.topo)

    def test_summary_counts(self):
        s = self.summary
        self.assertEqual((s["resources"], s["nodes"], s["routes"]), (3, 9, 13))
        self.assertEqual(s["factories"], 6)               # 4 suppliers + 2 assembly nodes
        self.assertEqual(s["route_modes"], 26)

    def test_placeholder_counts_match_parameters(self):
        for sql, params in self.cur.calls:
            self.assertEqual(sql.count("%s"), len(params), sql)

    def test_inventory_written_in_global_lock_order(self):
        rows = [p[:2] for s, p in self.cur.calls if s.startswith("INSERT IGNORE INTO Resource_Inventory")]
        self.assertEqual(rows, sorted(rows))
        self.assertEqual(len(rows), self.summary["inventory_rows"])

    def test_inventory_never_overwrites_live_ledger(self):
        sqls = [s for s, _ in self.cur.calls if "Resource_Inventory" in s]
        self.assertTrue(all(s.startswith("INSERT IGNORE") for s in sqls))

    def test_missing_routes_marked_removed_not_deleted(self):
        text = " ".join(s for s, _ in self.cur.calls)
        self.assertIn("UPDATE Route SET status='REMOVED'", text)
        self.assertNotIn("DELETE FROM Route ", text)

    def test_seed_runs_ddl_then_data(self):
        db = FakeDb()
        out = ts.seed(self.topo, db)
        self.assertEqual(len(db.cursors), 2)
        self.assertTrue(all(s.startswith("CREATE TABLE") for s, _ in db.cursors[0].calls))
        self.assertEqual(out["nodes"], 9)

    def test_ddl_file_splits_into_three_tables(self):
        stmts = ts.ddl_statements()
        self.assertEqual(len(stmts), 3)
        self.assertTrue(all("IF NOT EXISTS" in s for s in stmts))


if __name__ == "__main__":
    unittest.main()