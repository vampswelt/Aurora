import argparse
import itertools
import json
import logging
import queue
import socket
import threading
import time

import database as db
from optimizer import AllocationEngine

log = logging.getLogger("central")

HEARTBEAT_TIMEOUT = 15      
MAX_ATTEMPTS = 3           
RETRY_DELAY = 3.0
ALLOCATION_TTL = 120       

class NodeSession:
    def __init__(self, conn, addr):
        self.conn, self.addr = conn, addr
        self.node_id = None
        self.name = None
        self._lock = threading.Lock()

    def send(self, msg):
        data = (json.dumps(msg) + "\n").encode("utf-8")
        with self._lock:
            self.conn.sendall(data)


class SystemManager:
    def __init__(self, policy, workers, engine):
        self.policy, self.engine = policy, engine
        self.queue = queue.PriorityQueue()
        self._seq = itertools.count()         
        self.sessions, self.sessions_lock = {}, threading.Lock()
        self._threads = [threading.Thread(target=self._worker, name=f"sched-{i}", daemon=True)
                         for i in range(workers)]

    def start(self):
        for t in self._threads:
            t.start()

    def submit(self, process_id, priority, attempt=1):
        key = priority if self.policy == "PRIORITY" else 0
        self.queue.put((key, next(self._seq), process_id, priority, attempt))
        log.info("queued process %s (prio=%s, attempt=%d, depth=%d)",
                 process_id, priority, attempt, self.queue.qsize())

    def notify(self, node_id, msg):
        with self.sessions_lock:
            s = self.sessions.get(node_id)
        if s is None:
            return
        try:
            s.send(msg)
        except OSError:
            log.warning("could not reach node %s", node_id)

    def _worker(self):
        while True:
            _, _, pid, prio, attempt = self.queue.get()
            try:
                self._dispatch(pid, prio, attempt)
            except Exception:
                log.exception("allocation of process %s failed", pid)
            finally:
                self.queue.task_done()

    def _dispatch(self, pid, prio, attempt):
        res = db.allocate_process(pid, self.engine.plan_process, ALLOCATION_TTL)
        if res is None:
            return
        if res["status"] == "GRANTED":
            log.info("process %s GRANTED (%d allocations)", pid, len(res["allocations"]))
            self.notify(res["requester"], {"type": "ALLOCATION_GRANTED", "process_id": pid,
                                           "allocations": res["allocations"]})
            for a in res["allocations"]:
                self.notify(a["supplier_node_id"], {"type": "SHIP_ORDER", **a, "to_node": res["requester"]})
        elif attempt < MAX_ATTEMPTS:
            t = threading.Timer(RETRY_DELAY, self.submit, (pid, prio, attempt + 1))
            t.daemon = True
            t.start()
        else:
            db.deny_process(pid)
            log.warning("process %s DENIED after %d attempts", pid, attempt)
            self.notify(res["requester"], {"type": "ALLOCATION_DENIED", "process_id": pid})


class CommunicationManager:
    def __init__(self, host, port, manager):
        self.host, self.port, self.manager = host, port, manager

    def serve_forever(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(64)
        log.info("Central Command listening on %s:%d (policy=%s)", self.host, self.port, self.manager.policy)
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def _handle(self, conn, addr):
        session = NodeSession(conn, addr)
        try:
            with conn, conn.makefile("r", encoding="utf-8") as rfile:
                for line in rfile:
                    if not line.strip():
                        continue
                    try:
                        reply = self._process(session, json.loads(line))
                    except Exception as e:
                        log.warning("bad message from %s: %s", addr, e)
                        reply = {"type": "ERROR", "error": str(e)}
                    if reply:
                        session.send(reply)
        except OSError:
            pass
        finally:
            self._drop(session)

    def _drop(self, session):
        if session.node_id is None:
            return
        with self.manager.sessions_lock:
            if self.manager.sessions.get(session.node_id) is session:
                del self.manager.sessions[session.node_id]
        log.info("node %s disconnected", session.name)
        try:
            db.log_event(session.node_id, "NODE_DISCONNECTED", f"{session.name} closed connection", "WARNING")
        except Exception:
            pass

    def _process(self, s, msg):
        t = msg.get("type")
        if t == "REGISTER":
            ntype = msg["node_type"]
            if ntype not in ("FACTORY", "WAREHOUSE"):
                raise ValueError("node_type must be FACTORY or WAREHOUSE")
            node_id, inv = db.register_node(msg["node_name"], ntype, s.addr[0], s.addr[1],
                                            msg.get("location", ""), msg.get("capacities", {}),
                                            msg.get("inventory", {}))
            s.node_id, s.name = node_id, msg["node_name"]
            with self.manager.sessions_lock:
                self.manager.sessions[node_id] = s
            log.info("registered %s %s as node %d", ntype, s.name, node_id)
            return {"type": "REGISTER_OK", "node_id": node_id, "inventory": inv}
        if s.node_id is None:
            return {"type": "ERROR", "error": "REGISTER first"}
        if t == "HEARTBEAT":
            db.record_heartbeat(s.node_id, msg.get("memory_usage", 0), msg.get("active_processes", 0),
                                msg.get("inventory_delta", {}))
            return None
        if t == "REQUEST":
            items = [(i["resource"], float(i["quantity"])) for i in msg["items"]]
            prio = int(msg.get("priority", 5))
            pid = db.create_process(s.node_id, msg.get("process_name", "requisition"), prio, items,
                                    int(msg.get("required_by_seconds", 300)))
            self.manager.submit(pid, prio)
            return {"type": "REQUEST_QUEUED", "process_id": pid}
        if t == "RECEIPT":
            ok = db.confirm_receipt(int(msg["allocation_id"]), s.node_id)
            log.info("receipt for allocation %s from %s: %s", msg["allocation_id"], s.name,
                     "ok" if ok else "rejected")
            return None
        return {"type": "ERROR", "error": f"unknown message type {t}"}

def monitor(manager, interval=10):
    """Heartbeat watchdog + status line."""
    while True:
        time.sleep(interval)
        try:
            for name in db.mark_stale_nodes(HEARTBEAT_TIMEOUT):
                log.warning("node %s marked OFFLINE", name)
            nodes, totals = db.dashboard()
            log.info("NODES: %s", ", ".join(f"{n['node_name']}[{n['status']}]" for n in nodes) or "none")
            log.info("STOCK: %s | queue=%d", ", ".join(
                f"{t['resource_name']} avail={float(t['available']):.0f} resv={float(t['reserved']):.0f}"
                for t in totals), manager.queue.qsize())
        except Exception:
            log.exception("monitor error")

def main():
    ap = argparse.ArgumentParser(description="Central Command server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--policy", choices=["PRIORITY", "FCFS"], default="PRIORITY")
    ap.add_argument("--workers", type=int, default=2, help="scheduler worker threads")
    ap.add_argument("--optimizer-slots", type=int, default=1, help="max concurrent LP solves (semaphore)")
    ap.add_argument("--db-write-slots", type=int, default=4, help="max concurrent DB writers (semaphore)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    db.init(pool_size=max(8, a.workers * 2 + 4), write_slots=a.db_write_slots)
    manager = SystemManager(a.policy, a.workers, AllocationEngine(a.optimizer_slots))
    manager.start()
    threading.Thread(target=monitor, args=(manager,), daemon=True, name="monitor").start()
    try:
        CommunicationManager(a.host, a.port, manager).serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")

if __name__ == "__main__":
    main()