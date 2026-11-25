-- schema_topology.sql : additive tables that mirror the topology JSON (run by topology_seed.py)
-- The JSON file stays the single source of truth. These tables hold a queryable copy plus live route status.
-- Safe to run repeatedly (IF NOT EXISTS). Needs the core schema.sql tables to exist first.
-- topology_seed.py runs this on a connection already inside the right database (DB_NAME).
-- To run it by hand:  mysql -u root -p ogas_command < schema_topology.sql

-- One row per topology node. Node.node_name holds the topology id, display_name the readable name.
-- profile keeps supply, demand and storage settings as JSON so new fields never need a schema change.
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

-- One row per directed link between two nodes. status changes at runtime when a link is disrupted.
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

-- One row per transport mode on a route (air, sea, rail, road): keeps the schema normalized.
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