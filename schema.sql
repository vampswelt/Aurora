CREATE DATABASE IF NOT EXISTS ogas_command CHARACTER SET utf8mb4;
USE ogas_command;

CREATE TABLE IF NOT EXISTS Node (
  node_id        INT AUTO_INCREMENT PRIMARY KEY,
  node_name      VARCHAR(64) NOT NULL UNIQUE,
  ip_address     VARCHAR(45),
  port           INT,
  status         ENUM('ONLINE','OFFLINE','DEGRADED') NOT NULL DEFAULT 'OFFLINE',
  last_heartbeat DATETIME NULL,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Resource (
  resource_id    INT AUTO_INCREMENT PRIMARY KEY,
  resource_name  VARCHAR(64) NOT NULL UNIQUE,
  resource_type  VARCHAR(32) NOT NULL,
  unit           VARCHAR(16) NOT NULL,
  total_capacity DECIMAL(14,2) NOT NULL DEFAULT 0,
  description    VARCHAR(255),
  is_consumable  BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Factory (
  factory_id   INT AUTO_INCREMENT PRIMARY KEY,
  factory_name VARCHAR(64) NOT NULL,
  location     VARCHAR(128),
  node_id      INT NOT NULL UNIQUE,
  FOREIGN KEY (node_id) REFERENCES Node(node_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Warehouse (
  warehouse_id   INT AUTO_INCREMENT PRIMARY KEY,
  warehouse_name VARCHAR(64) NOT NULL,
  location       VARCHAR(128),
  node_id        INT NOT NULL UNIQUE,
  FOREIGN KEY (node_id) REFERENCES Node(node_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Factory_Capacity (
  capacity_id  INT AUTO_INCREMENT PRIMARY KEY,
  factory_id   INT NOT NULL,
  resource_id  INT NOT NULL,
  max_capacity DECIMAL(14,2) NOT NULL,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_factory_resource (factory_id, resource_id),
  FOREIGN KEY (factory_id)  REFERENCES Factory(factory_id),
  FOREIGN KEY (resource_id) REFERENCES Resource(resource_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `Process` (
  process_id   INT AUTO_INCREMENT PRIMARY KEY,
  process_name VARCHAR(64) NOT NULL,
  process_type VARCHAR(32) NOT NULL,
  status       ENUM('NEW','READY','RUNNING','DONE','FAILED') NOT NULL DEFAULT 'NEW',
  priority     INT NOT NULL DEFAULT 5,          
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  node_id      INT NOT NULL,
  FOREIGN KEY (node_id) REFERENCES Node(node_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Request (
  request_id   INT AUTO_INCREMENT PRIMARY KEY,
  request_type VARCHAR(64) NOT NULL,
  quantity     DECIMAL(14,2) NOT NULL,
  status       ENUM('PENDING','GRANTED','DENIED') NOT NULL DEFAULT 'PENDING',
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  process_id   INT NOT NULL,
  required_by  DATETIME NULL,
  FOREIGN KEY (process_id) REFERENCES `Process`(process_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Allocation (
  allocation_id      INT AUTO_INCREMENT PRIMARY KEY,
  request_id         INT NOT NULL,
  resource_id        INT NOT NULL,
  quantity_allocated DECIMAL(14,2) NOT NULL,
  allocated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  valid_until        DATETIME NULL,
  status             ENUM('ACTIVE','DELIVERED','EXPIRED') NOT NULL DEFAULT 'ACTIVE',
  node_id            INT NOT NULL,
  FOREIGN KEY (request_id)  REFERENCES Request(request_id),
  FOREIGN KEY (resource_id) REFERENCES Resource(resource_id),
  FOREIGN KEY (node_id)     REFERENCES Node(node_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Resource_Inventory (
  inventory_id       INT AUTO_INCREMENT PRIMARY KEY,
  resource_id        INT NOT NULL,
  available_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
  reserved_quantity  DECIMAL(14,2) NOT NULL DEFAULT 0,
  updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  node_id            INT NOT NULL,
  UNIQUE KEY uq_resource_node (resource_id, node_id),
  CHECK (available_quantity >= 0),
  CHECK (reserved_quantity >= 0),
  FOREIGN KEY (resource_id) REFERENCES Resource(resource_id),
  FOREIGN KEY (node_id)     REFERENCES Node(node_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS System_Status (
  status_id        INT AUTO_INCREMENT PRIMARY KEY,
  node_id          INT NOT NULL,
  snapshot_time    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  active_processes INT NOT NULL DEFAULT 0,
  memory_usage     DECIMAL(6,2) NOT NULL DEFAULT 0,
  status_details   TEXT,
  FOREIGN KEY (node_id) REFERENCES Node(node_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS System_Event (
  event_id          INT AUTO_INCREMENT PRIMARY KEY,
  event_type        VARCHAR(48) NOT NULL,
  event_description VARCHAR(255),
  severity          ENUM('INFO','WARNING','CRITICAL') NOT NULL DEFAULT 'INFO',
  event_time        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  node_id           INT NULL,
  FOREIGN KEY (node_id) REFERENCES Node(node_id)
) ENGINE=InnoDB;

INSERT IGNORE INTO Resource (resource_name, resource_type, unit, total_capacity, description, is_consumable) VALUES
  ('STEEL',   'RAW',      'kg',    100000, 'Structural steel',    TRUE),
  ('COPPER',  'RAW',      'kg',     50000, 'Copper wire stock',   TRUE),
  ('PLASTIC', 'RAW',      'kg',     80000, 'Plastic granules',    TRUE),
  ('WIDGETS', 'FINISHED', 'units', 200000, 'Finished widgets',    TRUE);