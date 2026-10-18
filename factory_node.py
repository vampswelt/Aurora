import argparse
import logging
import random
import threading
import time

from node_agent import LocalDataCollector, LocalProcessManager, NodeAgent, parse_kv

log = logging.getLogger("factory")
BATCHES_PER_ORDER = 5       
MAX_LOCAL_JOBS = 3          

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--location", default="Unknown")
    ap.add_argument("--produces", default="WIDGETS")
    ap.add_argument("--needs", default="STEEL=2,COPPER=1", help="recipe inputs per batch")
    ap.add_argument("--output-per-batch", type=float, default=1)
    ap.add_argument("--batch-time", type=float, default=4.0, help="seconds per batch")
    ap.add_argument("--stock", default="", help="initial stock, e.g. STEEL=10,COPPER=5")
    ap.add_argument("--capacity", type=float, default=500)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    needs, out_res = parse_kv(a.needs), a.produces.upper()
    caps = {r: a.capacity for r in [*needs, out_res]}
    collector = LocalDataCollector(parse_kv(a.stock), caps)
    pm = LocalProcessManager(workers=1)
    agent = NodeAgent(a.name, "FACTORY", a.host, a.port, a.location, collector, pm)
    state = {"pending": False, "retry_at": 0.0}

    def on_grant(msg):
        def deliver():
            for al in msg["allocations"]:
                collector.adjust(al["resource"], al["quantity"], track=False)
                agent.send_receipt(al["allocation_id"])
            log.info("received delivery for process %s", msg["process_id"])
            state["pending"] = False
        threading.Timer(random.uniform(2, 4), deliver).start()     

    def on_denied(msg):
        log.warning("request %s denied", msg["process_id"])
        state["pending"], state["retry_at"] = False, time.time() + 8

    def on_ship(msg):
        collector.adjust(msg["resource"], -msg["quantity"], track=False)
        log.info("shipping %s x%s to node %s", msg["resource"], msg["quantity"], msg["to_node"])

    agent.on("ALLOCATION_GRANTED", on_grant)
    agent.on("ALLOCATION_DENIED", on_denied)
    agent.on("SHIP_ORDER", on_ship)
    agent.connect()

    while agent.connected:
        time.sleep(1)
        if pm.load < MAX_LOCAL_JOBS and collector.try_consume(needs):
            pm.submit(f"{out_res}-batch", 5, a.batch_time,
                      lambda: collector.adjust(out_res, a.output_per_batch, track=True))
        low = {r: q * BATCHES_PER_ORDER - collector.get(r)
               for r, q in needs.items() if collector.get(r) < q * 2}
        if low and not state["pending"] and time.time() >= state["retry_at"]:
            blocked = any(collector.get(r) < q for r, q in needs.items())
            state["pending"] = True
            agent.request(low, priority=1 if blocked else 5, process_name=f"{a.name}-restock")
            log.info("requested %s (priority %d)", low, 1 if blocked else 5)

if __name__ == "__main__":
    main()