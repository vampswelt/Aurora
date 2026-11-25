"""Tests for the Central telemetry: database counters, scheduler counters, the publisher thread,
and that gui_server can read what Central publishes. No MySQL needed (a fake connection is used).
Run from the project folder:  python -m unittest discover -s tests
"""
import json
import os
import sys
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    import pymysql
except ImportError:                      # lets the tests run on a machine without PyMySQL
    pymysql = types.ModuleType("pymysql")
    pymysql.err = types.SimpleNamespace(OperationalError=type("OperationalError", (Exception,), {}))
    pymysql.connect = lambda **kw: None
    pymysql.cursors = types.SimpleNamespace(DictCursor=object)
    sys.modules["pymysql"] = pymysql
    sys.modules["pymysql.cursors"] = pymysql.cursors

import central_command
import database as db
import gui_server
import topology_loader as tl
from network_controller import NetworkController

DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "demo_topology.json")


class FakeConn:
    def begin(self): pass
    def commit(self): pass
    def rollback(self): pass

    def cursor(self):
        return self

    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakePool:
    def acquire(self): return FakeConn()
    def release(self, conn): pass


def reset_db_module(slots=2):
    db._pool, db._write_sem, db._write_slots = FakePool(), threading.Semaphore(slots), slots
    for k in db._stats:
        db._stats[k] = 0


class DatabaseStatsTests(unittest.TestCase):
    def setUp(self):
        reset_db_module()

    def test_counts_transactions_and_time(self):
        for _ in range(3):
            db.run_transaction(lambda cur: "ok")
        s = db.stats()
        self.assertEqual((s["transactions"], s["failures"], s["deadlock_retries"]), (3, 0, 0))
        self.assertEqual((s["write_slots"], s["write_slots_used"]), (2, 0))
        self.assertGreaterEqual(s["avg_tx_ms"], 0)

    def test_deadlock_is_rolled_back_retried_and_counted(self):
        calls = {"n": 0}

        def fn(cur):
            calls["n"] += 1
            if calls["n"] == 1:
                raise pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock")
            return "done"
        self.assertEqual(db.run_transaction(fn), "done")
        s = db.stats()
        self.assertEqual((calls["n"], s["deadlock_retries"], s["transactions"], s["failures"]), (2, 1, 1, 0))

    def test_gives_up_after_max_retries(self):
        def fn(cur):
            raise pymysql.err.OperationalError(1213, "Deadlock")
        with self.assertRaises(pymysql.err.OperationalError):
            db.run_transaction(fn, max_retries=2)
        s = db.stats()
        self.assertEqual((s["deadlock_retries"], s["failures"], s["transactions"]), (1, 1, 0))
        self.assertEqual(s["write_slots_used"], 0)

    def test_other_errors_count_as_failures_and_release_the_slot(self):
        with self.assertRaises(ValueError):
            db.run_transaction(lambda cur: (_ for _ in ()).throw(ValueError("boom")))
        with self.assertRaises(pymysql.err.OperationalError):
            db.run_transaction(lambda cur: (_ for _ in ()).throw(pymysql.err.OperationalError(1045, "denied")))
        s = db.stats()
        self.assertEqual((s["failures"], s["write_slots_used"]), (2, 0))

    def test_telemetry_writes_are_not_counted(self):
        db.run_transaction(lambda cur: None, count_stats=False)
        self.assertEqual(db.stats()["transactions"], 0)

    def test_write_slots_used_rises_while_a_writer_runs(self):
        seen = []
        db.run_transaction(lambda cur: seen.append(db.stats()["write_slots_used"]))
        db.run_transaction(lambda cur: seen.append(db.stats()["write_slots_used"]), write=False)
        self.assertEqual(seen, [1, 0])

    def test_semaphore_limits_concurrent_writers(self):
        reset_db_module(slots=2)
        peak, lock, running = [0], threading.Lock(), [0]

        def fn(cur):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.03)
            with lock:
                running[0] -= 1
        threads = [threading.Thread(target=db.run_transaction, args=(fn,)) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(peak[0], 2)
        self.assertEqual(db.stats()["transactions"], 8)


class SchedulerCounterTests(unittest.TestCase):
    def setUp(self):
        self.saved = (db.allocate_process, db.deny_process)
        central_command.RETRY_DELAY = 0.01
        self.manager = central_command.SystemManager("PRIORITY", 2, engine=types.SimpleNamespace(plan_process=None))

    def tearDown(self):
        db.allocate_process, db.deny_process = self.saved

    def test_counters_and_snapshot(self):
        outcomes = {1: {"status": "GRANTED", "requester": 9, "allocations": []},
                    2: {"status": "SHORTAGE", "requester": 9}, 3: None}
        db.allocate_process = lambda pid, planner, ttl: outcomes[pid]
        db.deny_process = lambda pid: None
        self.manager._dispatch(1, 5, 1)                                    # granted
        self.manager._dispatch(2, 5, 1)                                    # shortage -> retry scheduled
        self.manager._dispatch(2, 5, central_command.MAX_ATTEMPTS)         # shortage, out of attempts -> denied
        self.manager._dispatch(3, 5, 1)                                    # nothing to do: not counted
        snap = self.manager.snapshot()
        self.assertEqual((snap["dispatched"], snap["granted"], snap["denied"], snap["retries"]), (3, 1, 1, 1))
        self.assertEqual((snap["policy"], snap["workers"], snap["queue_depth"]), ("PRIORITY", 2, 0))
        self.assertIsNone(snap["aging_interval_s"])
        time.sleep(0.1)                                                    # the retry timer re-queued process 2
        self.assertEqual(self.manager.snapshot()["queue_depth"], 1)


class PublisherAndReaderContract(unittest.TestCase):
    """What Central publishes must be exactly what gui_server reads."""
    def test_published_details_reach_the_api(self):
        reset_db_module()
        db.run_transaction(lambda cur: None)
        db.run_transaction(lambda cur: None)
        saved = db.publish_central_status, db.prune_status
        published = []
        db.publish_central_status = lambda depth, mem, details: published.append((depth, mem, details))
        db.prune_status = lambda *a: None
        try:
            manager = central_command.SystemManager("FCFS", 3, engine=types.SimpleNamespace(plan_process=None))
            manager.submit(11, 1)
            manager.submit(12, 5)
            stop = threading.Event()
            threading.Thread(target=central_command.telemetry, args=(manager, 0.02, stop), daemon=True).start()
            deadline = time.time() + 3
            while not published and time.time() < deadline:
                time.sleep(0.02)
        finally:
            stop.set()
            db.publish_central_status, db.prune_status = saved
        self.assertTrue(published, "telemetry thread published nothing")
        depth, mem, details = published[0]
        self.assertEqual(depth, 2)
        self.assertEqual(sorted(details), ["database", "scheduler", "system", "time"])
        self.assertEqual(details["scheduler"]["policy"], "FCFS")
        self.assertEqual(details["database"]["transactions"], 2)
        for key in ("cpu_pct", "mem_pct", "threads", "connected_nodes", "uptime_s"):
            self.assertIn(key, details["system"])

        class Cursor:                                   # what MySQL would return to gui_server
            def execute(self, sql, params=()):
                self.sql = sql

            def fetchall(self):
                if "System_Status" in self.sql:
                    return [{"node_name": "CENTRAL", "active_processes": depth, "memory_usage": mem,
                             "status_details": json.dumps(details)}]
                if "Resource_Inventory" in self.sql:
                    return []
                return [{"node_name": "SUP-TW", "status": "ONLINE", "age": 1}]

            def __enter__(self): return self
            def __exit__(self, *a): return False

        class Conn:
            def cursor(self): return Cursor()
            def close(self): pass

        reader = gui_server.DbReader(connect=lambda: Conn())
        app = gui_server.App(NetworkController(tl.load_topology(DEMO)), reader)
        state = app.state()
        self.assertEqual(state["scheduler"]["policy"], "FCFS")
        self.assertEqual(state["scheduler"]["queue_depth"], 2)
        self.assertEqual(state["scheduler"]["workers"], 3)
        self.assertEqual(state["database"]["transactions"], 2)
        self.assertEqual(state["database"]["write_slots"], 2)
        self.assertEqual(state["database"]["deadlock_retries"], 0)
        self.assertTrue(state["database_connected"])


if __name__ == "__main__":
    unittest.main()