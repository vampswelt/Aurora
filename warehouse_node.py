import argparse
import logging
import time

from node_agent import LocalDataCollector, LocalProcessManager, NodeAgent, parse_kv

log = logging.getLogger("warehouse")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--location", default="Unknown")
    ap.add_argument("--stock", default="STEEL=200,COPPER=100,PLASTIC=150")
    ap.add_argument("--capacity", type=float, default=1000)
    ap.add_argument("--inbound-every", type=float, default=10, help="seconds between inbound deliveries")
    ap.add_argument("--inbound-qty", type=float, default=20, help="quantity per resource per delivery")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    stock = parse_kv(a.stock)
    collector = LocalDataCollector(stock, {r: a.capacity for r in stock})
    pm = LocalProcessManager(workers=1)
    agent = NodeAgent(a.name, "WAREHOUSE", a.host, a.port, a.location, collector, pm)

    def on_ship(msg):
        collector.adjust(msg["resource"], -msg["quantity"], track=False)
        log.info("shipping %s x%s to node %s (stock now %.0f)", msg["resource"], msg["quantity"],
                 msg["to_node"], collector.get(msg["resource"]))

    agent.on("SHIP_ORDER", on_ship)
    agent.connect()

    while agent.connected:
        time.sleep(a.inbound_every)
        for r in stock:
            pm.submit(f"unload-{r}", 3, 1.0, lambda r=r: collector.adjust(r, a.inbound_qty, track=True))
        log.info("inbound delivery scheduled; stock=%s", {k: round(v) for k, v in collector.snapshot().items()})

if __name__ == "__main__":
    main()