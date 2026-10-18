import json
import logging
import os
import queue
import random
import threading
import time
from contextlib import nullcontext

import pymysql
from pymysql.cursors import DictCursor
log = logging.getLogger("database")
DEADLOCK_ERRNOS = {1213, 1205}

_pool = None
_write_sem = None

class ConnectionPool:
    def __init__(self, size, **kwargs):
        self._q = queue.Queue(maxsize=size)
        for _ in range(size):
            self._q.put(pymysql.connect(cursorclass=DictCursor, autocommit=False,
                                        charset="utf8mb4", **kwargs))

    def acquire(self):
        conn = self._q.get()
        try:
            conn.ping(reconnect=True)
        except Exception:
            self._q.put(conn)
            raise
        return conn

    def release(self, conn):
        self._q.put(conn)


def init(pool_size=8, write_slots=4):
    global _pool, _write_sem
    _pool = ConnectionPool(
        pool_size,
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "ogas_command"),
    )
    _write_sem = threading.Semaphore(write_slots)
    log.info("DB pool=%d, write slots=%d", pool_size, write_slots)

def _attempt(fn, write):
    guard = _write_sem if write else nullcontext()
    with guard:                       
        conn = _pool.acquire()
        try:
            conn.begin()
            with conn.cursor() as cur:
                result = fn(cur)
            conn.commit()
            return result
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            _pool.release(conn)

def run_transaction(fn, write=True, max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            return _attempt(fn, write)
        except pymysql.err.OperationalError as e:
            if e.args and e.args[0] in DEADLOCK_ERRNOS and attempt < max_retries:
                delay = 0.05 * (2 ** attempt) + random.uniform(0, 0.05)
                log.warning("Deadlock/lock-timeout (%s), rolled back, retry %d/%d in %.2fs",
                            e.args[0], attempt, max_retries - 1, delay)
                time.sleep(delay)      # sleep OUTSIDE the semaphore
                continue
            raise


def _resource_map(cur):
    cur.execute("SELECT resource_id, resource_name FROM Resource")
    return {r["resource_name"]: r["resource_id"] for r in cur.fetchall()}

def register_node(name, node_type, ip, port, location, capacities, inventory):
    def txn(cur):
        rmap = _resource_map(cur)
        cur.execute(
            "INSERT INTO Node (node_name, ip_address, port, status, last_heartbeat) "
            "VALUES (%s,%s,%s,'ONLINE',NOW()) ON DUPLICATE KEY UPDATE "
            "ip_address=VALUES(ip_address), port=VALUES(port), status='ONLINE', "
            "last_heartbeat=NOW(), node_id=LAST_INSERT_ID(node_id)", (name, ip, port))
        node_id = cur.lastrowid
        table, col = ("Factory", "factory") if node_type == "FACTORY" else ("Warehouse", "warehouse")
        cur.execute(
            f"INSERT INTO {table} ({col}_name, location, node_id) VALUES (%s,%s,%s) "
            f"ON DUPLICATE KEY UPDATE {col}_name=VALUES({col}_name), location=VALUES(location)",
            (name, location, node_id))
        if node_type == "FACTORY":
            cur.execute("SELECT factory_id FROM Factory WHERE node_id=%s", (node_id,))
            fid = cur.fetchone()["factory_id"]
            for rname in sorted((n for n in capacities if n in rmap), key=rmap.get):
                cur.execute(
                    "INSERT INTO Factory_Capacity (factory_id, resource_id, max_capacity) "
                    "VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE max_capacity=VALUES(max_capacity)",
                    (fid, rmap[rname], capacities[rname]))
        names = sorted((n for n in set(inventory) | set(capacities) if n in rmap), key=rmap.get)
        for rname in names:                                    
            cur.execute("INSERT IGNORE INTO Resource_Inventory (resource_id,node_id,available_quantity) "
                        "VALUES (%s,%s,%s)", (rmap[rname], node_id, inventory.get(rname, 0)))
        cur.execute("INSERT INTO System_Event (event_type,event_description,severity,node_id) "
                    "VALUES ('NODE_REGISTERED',%s,'INFO',%s)", (f"{node_type} {name} online", node_id))
        cur.execute("SELECT r.resource_name, i.available_quantity FROM Resource_Inventory i "
                    "JOIN Resource r ON r.resource_id=i.resource_id WHERE i.node_id=%s", (node_id,))
        return node_id, {r["resource_name"]: float(r["available_quantity"]) for r in cur.fetchall()}
    return run_transaction(txn)


def record_heartbeat(node_id, memory_usage, active_processes, deltas):
    def txn(cur):
        rmap = _resource_map(cur)
        cur.execute("UPDATE Node SET last_heartbeat=NOW(), status='ONLINE' WHERE node_id=%s", (node_id,))
        cur.execute("INSERT INTO System_Status (node_id, active_processes, memory_usage, status_details) "
                    "VALUES (%s,%s,%s,%s)",
                    (node_id, active_processes, memory_usage, json.dumps({"inventory_delta": deltas})))
        for rname in sorted((n for n in deltas if n in rmap), key=rmap.get):
            d = deltas[rname]
            cur.execute(
                "INSERT INTO Resource_Inventory (resource_id,node_id,available_quantity) "
                "VALUES (%s,%s,GREATEST(%s,0)) ON DUPLICATE KEY UPDATE "
                "available_quantity=GREATEST(available_quantity+%s,0)", (rmap[rname], node_id, d, d))
    run_transaction(txn)

def mark_stale_nodes(timeout_s):
    def txn(cur):
        cur.execute("SELECT node_id, node_name FROM Node WHERE status='ONLINE' "
                    "AND last_heartbeat < NOW() - INTERVAL %s SECOND FOR UPDATE", (timeout_s,))
        stale = cur.fetchall()
        for n in stale:
            cur.execute("UPDATE Node SET status='OFFLINE' WHERE node_id=%s", (n["node_id"],))
            cur.execute("INSERT INTO System_Event (event_type,event_description,severity,node_id) "
                        "VALUES ('NODE_OFFLINE',%s,'WARNING',%s)",
                        (f"{n['node_name']} missed heartbeats", n["node_id"]))
        return [n["node_name"] for n in stale]
    return run_transaction(txn)

def log_event(node_id, event_type, description, severity="INFO"):
    run_transaction(lambda cur: cur.execute(
        "INSERT INTO System_Event (event_type,event_description,severity,node_id) VALUES (%s,%s,%s,%s)",
        (event_type, description[:255], severity, node_id)))

def create_process(node_id, name, priority, items, required_by_s):
    def txn(cur):
        rmap = _resource_map(cur)
        if not items:
            raise ValueError("request has no items")
        for rname, qty in items:
            if rname not in rmap:
                raise ValueError(f"unknown resource {rname}")
            if qty <= 0:
                raise ValueError("quantity must be > 0")
        cur.execute("INSERT INTO `Process` (process_name, process_type, status, priority, node_id) "
                    "VALUES (%s,'REQUISITION','READY',%s,%s)", (name, priority, node_id))
        pid = cur.lastrowid
        for rname, qty in items:
            cur.execute("INSERT INTO Request (request_type, quantity, status, process_id, required_by) "
                        "VALUES (%s,%s,'PENDING',%s,DATE_ADD(NOW(), INTERVAL %s SECOND))",
                        (rname, qty, pid, required_by_s))
        return pid
    return run_transaction(txn)


_SUPPLY_SQL = """
    SELECT i.resource_id, i.node_id, i.available_quantity,
           CASE WHEN w.warehouse_id IS NULL THEN 'FACTORY' ELSE 'WAREHOUSE' END AS node_type
    FROM Resource_Inventory i
    JOIN Node n ON n.node_id = i.node_id
    LEFT JOIN Warehouse w ON w.node_id = i.node_id
    WHERE i.resource_id IN ({ph}) AND n.status = 'ONLINE'
    ORDER BY i.resource_id, i.node_id
    FOR UPDATE OF i"""


def allocate_process(process_id, planner, ttl_s):
    def txn(cur):
        cur.execute("SELECT node_id FROM `Process` WHERE process_id=%s", (process_id,))
        proc = cur.fetchone()
        cur.execute(
            "SELECT r.request_id, r.request_type AS resource_name, r.quantity, res.resource_id "
            "FROM Request r JOIN Resource res ON res.resource_name = r.request_type "
            "WHERE r.process_id=%s AND r.status='PENDING' "
            "ORDER BY res.resource_id, r.request_id FOR UPDATE OF r", (process_id,))
        reqs = cur.fetchall()
        if not proc or not reqs:
            return None
        requester = proc["node_id"]
        rids = sorted({r["resource_id"] for r in reqs})
        cur.execute(_SUPPLY_SQL.format(ph=",".join(["%s"] * len(rids))), rids)
        plan = planner(requester, reqs, cur.fetchall())
        if plan is None:
            return {"status": "SHORTAGE", "requester": requester}
        allocations = []
        for r in reqs:
            for supplier, qty in plan[r["request_id"]]:
                cur.execute("UPDATE Resource_Inventory SET available_quantity=available_quantity-%s, "
                            "reserved_quantity=reserved_quantity+%s WHERE resource_id=%s AND node_id=%s",
                            (qty, qty, r["resource_id"], supplier))
                cur.execute("INSERT INTO Allocation (request_id, resource_id, quantity_allocated, "
                            "valid_until, status, node_id) VALUES (%s,%s,%s,"
                            "DATE_ADD(NOW(), INTERVAL %s SECOND),'ACTIVE',%s)",
                            (r["request_id"], r["resource_id"], qty, ttl_s, supplier))
                allocations.append({"allocation_id": cur.lastrowid, "resource": r["resource_name"],
                                    "supplier_node_id": supplier, "quantity": qty})
            cur.execute("UPDATE Request SET status='GRANTED' WHERE request_id=%s", (r["request_id"],))
        cur.execute("UPDATE `Process` SET status='RUNNING' WHERE process_id=%s", (process_id,))
        return {"status": "GRANTED", "requester": requester, "allocations": allocations}
    return run_transaction(txn)


def deny_process(process_id):
    def txn(cur):
        cur.execute("UPDATE Request SET status='DENIED' WHERE process_id=%s AND status='PENDING'", (process_id,))
        cur.execute("UPDATE `Process` SET status='FAILED' WHERE process_id=%s", (process_id,))
        cur.execute("SELECT node_id FROM `Process` WHERE process_id=%s", (process_id,))
        node_id = cur.fetchone()["node_id"]
        cur.execute("INSERT INTO System_Event (event_type,event_description,severity,node_id) "
                    "VALUES ('REQUEST_DENIED',%s,'WARNING',%s)",
                    (f"process {process_id} denied: insufficient supply", node_id))
    run_transaction(txn)


def confirm_receipt(allocation_id, requester_id):
    def txn(cur):
        cur.execute(
            "SELECT a.resource_id, a.quantity_allocated, a.node_id AS supplier, a.status, "
            "p.node_id AS requester, p.process_id FROM Allocation a "
            "JOIN Request rq ON rq.request_id=a.request_id "
            "JOIN `Process` p ON p.process_id=rq.process_id "
            "WHERE a.allocation_id=%s FOR UPDATE OF a", (allocation_id,))
        a = cur.fetchone()
        if not a or a["requester"] != requester_id or a["status"] != "ACTIVE":
            return False
        rid, q = a["resource_id"], a["quantity_allocated"]
        cur.execute("INSERT IGNORE INTO Resource_Inventory (resource_id,node_id,available_quantity) "
                    "VALUES (%s,%s,0)", (rid, requester_id))
        cur.execute("SELECT inventory_id FROM Resource_Inventory WHERE resource_id=%s "
                    "AND node_id IN (%s,%s) ORDER BY node_id FOR UPDATE", (rid, a["supplier"], requester_id))
        cur.execute("UPDATE Resource_Inventory SET reserved_quantity=GREATEST(reserved_quantity-%s,0) "
                    "WHERE resource_id=%s AND node_id=%s", (q, rid, a["supplier"]))
        cur.execute("UPDATE Resource_Inventory SET available_quantity=available_quantity+%s "
                    "WHERE resource_id=%s AND node_id=%s", (q, rid, requester_id))
        cur.execute("UPDATE Allocation SET status='DELIVERED' WHERE allocation_id=%s", (allocation_id,))
        cur.execute("SELECT COUNT(*) AS n FROM Allocation a JOIN Request rq ON rq.request_id=a.request_id "
                    "WHERE rq.process_id=%s AND a.status='ACTIVE'", (a["process_id"],))
        if cur.fetchone()["n"] == 0:
            cur.execute("UPDATE `Process` SET status='DONE' WHERE process_id=%s", (a["process_id"],))
        return True
    return run_transaction(txn)

def dashboard():
    def txn(cur):
        cur.execute("SELECT node_name, status, TIMESTAMPDIFF(SECOND,last_heartbeat,NOW()) AS age "
                    "FROM Node ORDER BY node_id")
        nodes = cur.fetchall()
        cur.execute("SELECT r.resource_name, SUM(i.available_quantity) AS available, "
                    "SUM(i.reserved_quantity) AS reserved FROM Resource_Inventory i "
                    "JOIN Resource r ON r.resource_id=i.resource_id "
                    "GROUP BY r.resource_id, r.resource_name ORDER BY r.resource_id")
        return nodes, cur.fetchall()
    return run_transaction(txn, write=False)