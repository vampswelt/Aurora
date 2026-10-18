import itertools
import json
import logging
import queue
import socket
import threading
import time
from collections import defaultdict

import psutil

log = logging.getLogger("node")

def parse_kv(text):
    out = {}
    for part in filter(None, (text or "").split(",")):
        k, v = part.split("=")
        out[k.strip().upper()] = float(v)
    return out

class LocalDataCollector:
    def __init__(self, inventory, capacities):
        self._inv = defaultdict(float, inventory)
        self.capacities = capacities
        self._pending = defaultdict(float)
        self._lock = threading.Lock()

    def set_all(self, snapshot):
        with self._lock:
            self._inv = defaultdict(float, snapshot)

    def get(self, resource):
        with self._lock:
            return self._inv[resource]

    def snapshot(self):
        with self._lock:
            return dict(self._inv)

    def adjust(self, resource, delta, track=True):
        with self._lock:
            new = max(0.0, self._inv[resource] + delta)
            if track:
                new = min(new, self.capacities.get(resource, float("inf")))
            applied = new - self._inv[resource]
            self._inv[resource] = new
            if track:
                self._pending[resource] += applied
            return applied

    def try_consume(self, needs):
        with self._lock:
            if any(self._inv[r] < q for r, q in needs.items()):
                return False
            for r, q in needs.items():
                self._inv[r] -= q
                self._pending[r] -= q
            return True

    def drain_deltas(self):
        with self._lock:
            d = {r: v for r, v in self._pending.items() if v}
            self._pending = defaultdict(float)
            return d

    def restore_deltas(self, deltas):
        with self._lock:
            for r, v in deltas.items():
                self._pending[r] += v


class LocalProcessManager:
    def __init__(self, workers=1):
        self._q = queue.PriorityQueue()
        self._seq = itertools.count()
        self._load = 0
        self._lock = threading.Lock()
        for i in range(workers):
            threading.Thread(target=self._run, name=f"local-proc-{i}", daemon=True).start()

    @property
    def load(self):
        with self._lock:
            return self._load

    def submit(self, name, priority, duration, on_done):
        with self._lock:
            self._load += 1
        self._q.put((priority, next(self._seq), name, duration, on_done))

    def _run(self):
        while True:
            prio, seq, name, duration, on_done = self._q.get()
            start = time.time()
            time.sleep(duration)                      
            try:
                on_done()
            except Exception:
                log.exception("job %s failed", name)
            finally:
                log.info("GANTT|%d|%s|%d|%.3f|%.3f", seq + 1, name, prio, start, time.time())
                with self._lock:
                    self._load -= 1

class NodeAgent:
    def __init__(self, name, node_type, host, port, location, collector, process_mgr, hb_interval=5.0):
        self.name, self.node_type, self.location = name, node_type, location
        self.host, self.port = host, port
        self.collector, self.pm, self.hb_interval = collector, process_mgr, hb_interval
        self.node_id = None
        self.connected = False
        self._handlers = {}
        self._send_lock = threading.Lock()

    def on(self, msg_type, fn):
        self._handlers[msg_type] = fn

    def _send(self, msg):
        data = (json.dumps(msg) + "\n").encode("utf-8")
        with self._send_lock:
            self.sock.sendall(data)

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.settimeout(None)
        self._rfile = self.sock.makefile("r", encoding="utf-8")
        self._send({"type": "REGISTER", "node_name": self.name, "node_type": self.node_type,
                    "location": self.location, "capacities": self.collector.capacities,
                    "inventory": self.collector.snapshot()})
        reply = json.loads(self._rfile.readline())
        if reply.get("type") != "REGISTER_OK":
            raise RuntimeError(f"registration failed: {reply}")
        self.node_id = reply["node_id"]
        self.collector.set_all(reply["inventory"])   
        self.connected = True
        log.info("registered as node %d, stock=%s", self.node_id, self.collector.snapshot())
        threading.Thread(target=self._rx_loop, daemon=True, name="rx").start()
        threading.Thread(target=self._hb_loop, daemon=True, name="heartbeat").start()

    def _rx_loop(self):
        try:
            for line in self._rfile:
                msg = json.loads(line)
                fn = self._handlers.get(msg.get("type"))
                if fn:
                    fn(msg)
                else:
                    log.info("recv %s", msg)
        except (OSError, ValueError):
            pass
        self.connected = False
        log.error("connection to Central lost")

    def _hb_loop(self):
        proc = psutil.Process()
        while self.connected:
            time.sleep(self.hb_interval)
            deltas = self.collector.drain_deltas()
            try:
                self._send({"type": "HEARTBEAT", "memory_usage": round(proc.memory_percent(), 2),
                            "active_processes": self.pm.load, "inventory_delta": deltas})
            except OSError:
                self.collector.restore_deltas(deltas)
                self.connected = False

    def request(self, items, priority, process_name="requisition", required_by_seconds=300):
        """items: {resource: quantity}. Result arrives asynchronously (ALLOCATION_GRANTED/DENIED)."""
        self._send({"type": "REQUEST", "process_name": process_name, "priority": priority,
                    "required_by_seconds": required_by_seconds,
                    "items": [{"resource": r, "quantity": q} for r, q in items.items()]})

    def send_receipt(self, allocation_id):
        self._send({"type": "RECEIPT", "allocation_id": allocation_id})