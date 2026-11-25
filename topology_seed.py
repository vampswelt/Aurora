"""
topology_seed.py : mirrors the topology JSON into MySQL. Idempotent: safe to run on every start.

  python topology_seed.py                         # seed from TOPOLOGY_PATH or config/demo_topology.json
  python topology_seed.py --topology config/apple_global_topology.json
  python topology_seed.py --dry-run               # validate and summarise only, no database needed

What it writes (nothing is hardcoded, it all comes from the JSON):
  Resource, Node, Node_Topology, Factory (supplier/assembly nodes), Factory_Capacity,
  Resource_Inventory (initial stock, INSERT IGNORE so a live ledger is never overwritten),
  Route, Route_Mode, and a TOPOLOGY_LOADED row in System_Event.
Routes missing from the JSON are marked REMOVED, never deleted. Route status is reset to the JSON value.
Lock order stays Node -> Factory -> Factory_Capacity -> Resource_Inventory (sorted) -> System_Event.
"""
import argparse
import json
import logging
import os
import sys

from topology_loader import TopologyError, describe, load_topology

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DDL_FILE = os.path.join(BASE_DIR, "schema_topology.sql")   # optional: for running the SQL by hand
FACTORY_ROLES = ("SUPPLIER", "ASSEMBLY")      # roles that get a row in the ER diagram's Factory table
log = logging.getLogger("topology_seed")


# Same SQL as schema_topology.sql, embedded so seeding never depends on reading that file.
DDL = """
CREATE TABLE IF NOT EXISTS Node_Topology (
  node_id      INT PRIMARY KEY,
  display_name VARCHAR(120) NOT NULL,
  role         VARCHAR(24)  NOT NULL,
  latitude     DECIMAL(9,6) NOT NULL,
  longitude    DECIMAL(9,6) NOT NULL,
  profile      JSON NOT NULL,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (node_id) REFERENCES Node(node_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Route (
  route_id     INT AUTO_INCREMENT PRIMARY KEY,
  code         VARCHAR(64) NOT NULL UNIQUE,
  from_node_id INT NOT NULL,
  to_node_id   INT NOT NULL,
  status       ENUM('ACTIVE','DISRUPTED','REMOVED') NOT NULL DEFAULT 'ACTIVE',
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (from_node_id) REFERENCES Node(node_id),
  FOREIGN KEY (to_node_id)   REFERENCES Node(node_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Route_Mode (
  route_mode_id    INT AUTO_INCREMENT PRIMARY KEY,
  route_id         INT NOT NULL,
  mode             VARCHAR(16) NOT NULL,
  cost_per_unit    DECIMAL(12,4) NOT NULL,
  transit_days     DECIMAL(8,2)  NOT NULL,
  capacity_per_day DECIMAL(14,2) NOT NULL,
  UNIQUE KEY uq_route_mode (route_id, mode),
  FOREIGN KEY (route_id) REFERENCES Route(route_id)
) ENGINE=InnoDB;
"""


def ddl_statements(path=None):
    """The CREATE TABLE statements: from path if given, otherwise the embedded copy above."""
    if path:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = DDL
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def apply_topology(cur, topo):
    """Write the whole topology inside one transaction. Returns a summary dict."""
    for res in sorted(topo.resources.values(), key=lambda r: r.id):
        cur.execute(
            "INSERT INTO Resource (resource_name, resource_type, unit, total_capacity, description, is_consumable) "
            "VALUES (%s,%s,%s,%s,%s,%s) AS nv ON DUPLICATE KEY UPDATE resource_type=nv.resource_type, "
            "unit=nv.unit, total_capacity=nv.total_capacity, description=nv.description, "
            "is_consumable=nv.is_consumable",
            (res.id, res.type, res.unit,
             sum(n.supply.get(res.id, {}).get("capacity_per_day", 0) for n in topo.nodes.values()),
             res.description, res.is_consumable))
    cur.execute("SELECT resource_id, resource_name FROM Resource")
    rmap = {row["resource_name"]: row["resource_id"] for row in cur.fetchall()}

    nmap, factories = {}, 0
    for node in sorted(topo.nodes.values(), key=lambda n: n.id):
        cur.execute("INSERT INTO Node (node_name, status) VALUES (%s,'OFFLINE') "
                    "ON DUPLICATE KEY UPDATE node_id=LAST_INSERT_ID(node_id)", (node.id,))
        nmap[node.id] = cur.lastrowid
        profile = {"supply": node.supply, "demand": node.demand, "storage": node.storage}
        cur.execute(
            "INSERT INTO Node_Topology (node_id, display_name, role, latitude, longitude, profile) "
            "VALUES (%s,%s,%s,%s,%s,%s) AS nv ON DUPLICATE KEY UPDATE display_name=nv.display_name, "
            "role=nv.role, latitude=nv.latitude, longitude=nv.longitude, profile=nv.profile",
            (nmap[node.id], node.name, node.role, node.lat, node.lon, json.dumps(profile)))
        if node.role in FACTORY_ROLES:
            factories += 1
            cur.execute("INSERT INTO Factory (factory_name, location, node_id) VALUES (%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE factory_name=VALUES(factory_name), location=VALUES(location)",
                        (node.name, node.location or f"{node.lat:.4f},{node.lon:.4f}", nmap[node.id]))
            cur.execute("SELECT factory_id FROM Factory WHERE node_id=%s", (nmap[node.id],))
            factory_id = cur.fetchone()["factory_id"]
            for res_id, spec in sorted(node.supply.items(), key=lambda kv: rmap[kv[0]]):
                cur.execute("INSERT INTO Factory_Capacity (factory_id, resource_id, max_capacity) VALUES (%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE max_capacity=VALUES(max_capacity)",
                            (factory_id, rmap[res_id], spec["capacity_per_day"]))

    inventory = {}
    for node in topo.nodes.values():
        for res_id in set(node.stock) | set(node.supply):
            inventory[(rmap[res_id], nmap[node.id])] = node.stock.get(res_id, 0.0)
    for (resource_id, node_id), qty in sorted(inventory.items()):          # global lock order
        cur.execute("INSERT IGNORE INTO Resource_Inventory (resource_id, node_id, available_quantity) "
                    "VALUES (%s,%s,%s)", (resource_id, node_id, qty))

    modes = 0
    for route in sorted(topo.routes.values(), key=lambda r: r.id):
        cur.execute("INSERT INTO Route (code, from_node_id, to_node_id, status) VALUES (%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE from_node_id=VALUES(from_node_id), to_node_id=VALUES(to_node_id), "
                    "status=VALUES(status), route_id=LAST_INSERT_ID(route_id)",
                    (route.id, nmap[route.src], nmap[route.dst], route.status))
        route_id = cur.lastrowid
        names = sorted(route.modes)
        cur.execute("DELETE FROM Route_Mode WHERE route_id=%s AND mode NOT IN (" + ",".join(["%s"] * len(names)) + ")",
                    (route_id, *names))
        for name in names:
            m = route.modes[name]
            modes += 1
            cur.execute("INSERT INTO Route_Mode (route_id, mode, cost_per_unit, transit_days, capacity_per_day) "
                        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE cost_per_unit=VALUES(cost_per_unit), "
                        "transit_days=VALUES(transit_days), capacity_per_day=VALUES(capacity_per_day)",
                        (route_id, name, m.cost_per_unit, m.transit_days, m.capacity_per_day))
    codes = sorted(topo.routes)
    cur.execute("UPDATE Route SET status='REMOVED' WHERE code NOT IN (" + ",".join(["%s"] * len(codes)) + ")",
                tuple(codes))

    cur.execute("INSERT INTO System_Event (event_type, event_description, severity, node_id) "
                "VALUES ('TOPOLOGY_LOADED', %s, 'INFO', NULL)",
                (f"{topo.meta['name']} sha256={topo.fingerprint[:12]} nodes={len(topo.nodes)} "
                 f"routes={len(topo.routes)}"[:255],))
    return {"resources": len(topo.resources), "nodes": len(nmap), "factories": factories,
            "inventory_rows": len(inventory), "routes": len(topo.routes), "route_modes": modes}


def seed(topo, dbm=None):
    """Create the topology tables if needed, then write the topology. dbm defaults to database.py."""
    if dbm is None:
        import database as dbm
    statements = ddl_statements()

    def create_tables(cur):
        for stmt in statements:
            cur.execute(stmt)
    dbm.run_transaction(create_tables)
    return dbm.run_transaction(lambda cur: apply_topology(cur, topo))


def main():
    ap = argparse.ArgumentParser(description="Seed MySQL from the network topology JSON")
    ap.add_argument("--topology", help="path to the topology JSON (default: TOPOLOGY_PATH or config/demo_topology.json)")
    ap.add_argument("--dry-run", action="store_true", help="validate and summarise only, no database needed")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        topo = load_topology(args.topology)
    except TopologyError as e:
        print(e)
        sys.exit(1)
    print(describe(topo))
    if args.dry_run:
        return
    import database as dbm
    dbm.init(pool_size=2, write_slots=1)
    print("Seeded:", seed(topo, dbm))


if __name__ == "__main__":
    main()