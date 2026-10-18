import getpass
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "ogas_config.json")
KNOWN_RESOURCES = ("STEEL", "COPPER", "PLASTIC", "CARS")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
GANTT_RE = re.compile(r"GANTT\|(\d+)\|([^|]+)\|(\d+)\|([\d.]+)\|([\d.]+)")

STATE = {
    "central": {"host": "127.0.0.1", "port": 5000, "policy": "PRIORITY", "workers": 2},
    "warehouses": [],  
    "factories": [],    
}
RUN = {"procs": []}                      
LOGS, LOG_LOCK, LOG_SEQ = deque(maxlen=5000), threading.Lock(), 0
JOBS, JOB_LOCK = [], threading.Lock()    

def  ask(prompt, default=None, convert=str, check=None, err="Invalid input, try again."):
    while True:
        raw = input(f"{prompt}" + (f" [{default}]" if default is not None else "") + ": ").strip()
        if raw == "" and default is not None:
            raw = str(default)
        if raw == "":
            print("Please enter a value.")
            continue
        try:
            value = convert(raw)
            if check and not check(value):
                raise ValueError(err)
            return value
        except ValueError as e:
            print(str(e) or err)

def parse_resources(text, allow_empty=False, positive=True):
    out = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        if "=" not in part:
            raise ValueError(f"'{part}' should look like STEEL=100")
        key, val = (x.strip() for x in part.split("=", 1))
        key = key.upper()
        if key not in KNOWN_RESOURCES:
            raise ValueError(f"unknown resource '{key}'. Use: {', '.join(KNOWN_RESOURCES)}")
        try:
            qty = float(val)
        except ValueError:
            raise ValueError(f"'{val}' is not a number")
        if qty < 0 or (positive and qty == 0):
            raise ValueError("quantities must be " + ("greater than 0" if positive else "0 or more"))
        out[key] = qty
    if not out and not allow_empty:
        raise ValueError("enter at least one resource, e.g. STEEL=100")
    return out

def kv(d):
    return ",".join(f"{k}={v:g}" for k, v in d.items())

def expanded_names(f):
    n = f.get("count", 1)
    return [f["name"]] if n == 1 else [f"{f['name']}-{i}" for i in range(1, n + 1)]

def taken_names():
    names = {w["name"] for w in STATE["warehouses"]}
    for f in STATE["factories"]:
        names.update(expanded_names(f))
    return names

def print_table(headers, rows):
    if not rows:
        print("  (none)")
        return
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  " + "-+-".join("-" * w for w in widths))
    for r in rows:
        print("  " + " | ".join(c.ljust(w) for c, w in zip(r, widths)))

def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(STATE, fh, indent=2)

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            STATE["central"].update(data.get("central", {}))
            STATE["warehouses"] = data.get("warehouses", [])
            STATE["factories"] = data.get("factories", [])
            print("Loaded saved configuration from ogas_config.json")
        except (OSError, ValueError):
            print("Could not read ogas_config.json, starting empty.")

def cmd_central():
    c = STATE["central"]
    print("\n-- Command node settings --")
    c["host"] = ask("Host/IP", c["host"])
    c["port"] = ask("TCP port", c["port"], int, lambda v: 1024 <= v <= 65535, "Port must be 1024-65535.")
    c["policy"] = ask("Scheduling policy (PRIORITY or FCFS)", c["policy"], lambda s: s.upper(),
                      lambda v: v in ("PRIORITY", "FCFS"), "Type PRIORITY or FCFS.")
    c["workers"] = ask("Scheduler worker threads", c["workers"], int, lambda v: 1 <= v <= 16, "Use 1-16.")
    save_config()
    print("Saved." + (" Restart the system to apply." if RUN["procs"] else ""))

def cmd_add_warehouse():
    print("\n-- Add warehouse --")
    name = ask("Name", f"Warehouse-{len(STATE['warehouses']) + 1}",
               check=lambda v: NAME_RE.match(v) and v not in taken_names(),
               err="Use letters, digits, - or _ (max 40) and a name not already used.")
    location = ask("Location", "Unknown")
    stock = ask(f"Starting stock, e.g. STEEL=200,COPPER=100 (resources: {', '.join(KNOWN_RESOURCES)})",
                convert=lambda s: parse_resources(s, positive=False))
    STATE["warehouses"].append({"name": name, "location": location, "stock": stock})
    save_config()
    print(f"Added {name}.")

def cmd_add_factory():
    print("\n-- Add factory --")
    name = ask("Name", f"Factory-{len(STATE['factories']) + 1}",
               check=lambda v: NAME_RE.match(v) and v not in taken_names(),
               err="Use letters, digits, - or _ (max 40) and a name not already used.")
    location = ask("Location", "Unknown")
    produces = ask("Resource it produces", "CARS", lambda s: s.upper(),
                   lambda v: v in KNOWN_RESOURCES, f"Choose from: {', '.join(KNOWN_RESOURCES)}")
    needs = ask("Inputs per batch, e.g. STEEL=2,COPPER=1", "STEEL=2,COPPER=1", parse_resources)
    stock = ask("Starting stock (e.g. STEEL=4,COPPER=2)", "STEEL=4,COPPER=2",
                lambda s: parse_resources(s, allow_empty=True, positive=False))
    batch_time = ask("Seconds per production batch", 4, float, lambda v: v > 0, "Must be greater than 0.")
    count = ask("How many identical instances", 1, int, lambda v: 1 <= v <= 20, "Use 1-20.")
    factory = {"name": name, "location": location, "produces": produces, "needs": needs,
               "stock": stock, "batch_time": batch_time, "count": count}
    clash = set(expanded_names(factory)) & {w["name"] for w in STATE["warehouses"]}
    for f in STATE["factories"]:
        clash |= set(expanded_names(factory)) & set(expanded_names(f))
    if clash:
        print(f"Name clash with existing entries: {', '.join(sorted(clash))}. Not added.")
        return
    STATE["factories"].append(factory)
    save_config()
    print(f"Added {', '.join(expanded_names(factory))}.")

def cmd_show():
    c = STATE["central"]
    print(f"\nCommand node: {c['host']}:{c['port']}  policy={c['policy']}  workers={c['workers']}")
    print("\nWarehouses:")
    print_table(["Name", "Location", "Stock"],
                [[w["name"], w["location"], kv(w["stock"])] for w in STATE["warehouses"]])
    print("\nFactories:")
    print_table(["Name", "Location", "Produces", "Needs/batch", "Stock", "Batch s", "Instances"],
                [[f["name"], f["location"], f["produces"], kv(f["needs"]), kv(f["stock"]) or "-",
                  f"{f['batch_time']:g}", f["count"]] for f in STATE["factories"]])

def cmd_remove():
    names = [w["name"] for w in STATE["warehouses"]] + [f["name"] for f in STATE["factories"]]
    if not names:
        print("Nothing to remove.")
        return
    print("Entries: " + ", ".join(names))
    target = ask("Name to remove (exact)")
    before = len(names)
    STATE["warehouses"] = [w for w in STATE["warehouses"] if w["name"] != target]
    STATE["factories"] = [f for f in STATE["factories"] if f["name"] != target]
    if len(STATE["warehouses"]) + len(STATE["factories"]) == before:
        print("No entry with that name.")
        return
    save_config()
    print(f"Removed {target}." + (" Restart the system to apply." if RUN["procs"] else ""))

def cmd_demo():
    if (STATE["warehouses"] or STATE["factories"]) and \
            ask("This replaces the current configuration. Continue? (y/n)", "n", str.lower,
                lambda v: v in ("y", "n")) == "n":
        return
    STATE["central"] = {"host": "127.0.0.1", "port": 5000, "policy": "PRIORITY", "workers": 2}
    STATE["warehouses"] = [
        {"name": "Warehouse-1", "location": "North", "stock": {"STEEL": 200, "COPPER": 100, "PLASTIC": 150}},
        {"name": "Warehouse-2", "location": "South", "stock": {"STEEL": 50, "COPPER": 300}},
    ]
    STATE["factories"] = [
        {"name": "Factory-A", "location": "East", "produces": "CARS", "needs": {"STEEL": 2, "COPPER": 1},
         "stock": {"STEEL": 4, "COPPER": 2}, "batch_time": 4, "count": 1},
        {"name": "Factory-B", "location": "West", "produces": "CARS", "needs": {"STEEL": 3, "COPPER": 2},
         "stock": {"STEEL": 3}, "batch_time": 6, "count": 1},
    ]
    save_config()
    print("Demo configuration loaded (2 warehouses, 2 factories).")

def ensure_password():
    if "DB_PASSWORD" not in os.environ:
        os.environ["DB_PASSWORD"] = getpass.getpass("MySQL password for the DB user (Enter if none): ")

def db_connect(database=None):
    import pymysql
    return pymysql.connect(host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3306")),
                           user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""),
                           database=database, autocommit=True)

def init_db():
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if not ln.strip().startswith("--")]
    statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
    if not statements:
        raise RuntimeError("schema.sql is empty. Paste it again and save the file.")
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
    finally:
        conn.close()

def query(sql, params=()):
    conn = db_connect(os.getenv("DB_NAME", "ogas_command"))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [d[0] for d in cur.description], cur.fetchall()
    finally:
        conn.close()

def build_commands():
    c, py = STATE["central"], [sys.executable, "-u"]
    cmds = [("central", py + ["central_command.py", "--host", c["host"], "--port", str(c["port"]),
                              "--policy", c["policy"], "--workers", str(c["workers"])])]
    conn = ["--host", c["host"], "--port", str(c["port"])]
    for w in STATE["warehouses"]:
        cmds.append((w["name"], py + ["warehouse_node.py", "--name", w["name"], "--location", w["location"],
                                      "--stock", kv(w["stock"])] + conn))
    for f in STATE["factories"]:
        for name in expanded_names(f):
            cmds.append((name, py + ["factory_node.py", "--name", name, "--location", f["location"],
                                     "--produces", f["produces"], "--needs", kv(f["needs"]),
                                     "--stock", kv(f["stock"]), "--batch-time", f"{f['batch_time']:g}"] + conn))
    return cmds

def pump(label, proc):
    global LOG_SEQ
    for line in proc.stdout:
        line = line.rstrip()
        m = GANTT_RE.search(line)
        if m:
            with JOB_LOCK:
                JOBS.append({"node": label, "id": int(m.group(1)), "name": m.group(2), "prio": int(m.group(3)),
                             "start": float(m.group(4)), "end": float(m.group(5))})
            continue
        with LOG_LOCK:
            LOG_SEQ += 1
            LOGS.append((LOG_SEQ, f"[{label}] {line}"))

def spawn(label, argv):
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    kwargs = dict(cwd=BASE_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  text=True, encoding="utf-8", errors="replace", bufsize=1)
    if os.name == "nt":      
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    RUN["procs"].append((label, proc))
    threading.Thread(target=pump, args=(label, proc), daemon=True).start()
    return proc

def wait_for_port(host, port, proc, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            socket.create_connection(("127.0.0.1" if host == "0.0.0.0" else host, port), timeout=1).close()
            return True
        except OSError:
            time.sleep(0.3)
    return False

def stop_system(quiet=False):
    procs, RUN["procs"] = RUN["procs"], []
    for _, p in procs:
        if p.poll() is None:
            p.terminate()
    deadline = time.time() + 5
    for _, p in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
    if procs and not quiet:
        print(f"Stopped {len(procs)} processes.")

def refresh_procs():
    for label, p in list(RUN["procs"]):
        if p.poll() is not None:
            print(f"[main] {label} exited with code {p.returncode} (see option 9 for its log)")
            RUN["procs"].remove((label, p))

def cmd_start():
    if RUN["procs"]:
        print("System is already running. Stop it first (option s).")
        return
    if not STATE["warehouses"] or not STATE["factories"]:
        print("Add at least one warehouse and one factory first (options 2 and 3, or d for a demo).")
        return
    ensure_password()
    try:
        init_db()
    except Exception as e:
        print(f"Database problem: {e}\nCheck that MySQL is running and the password is right.")
        os.environ.pop("DB_PASSWORD", None)
        return
    with JOB_LOCK:
        JOBS.clear()
    with LOG_LOCK:
        LOGS.clear()
    cmds = build_commands()
    c = STATE["central"]
    for i, (label, argv) in enumerate(cmds):
        proc = spawn(label, argv)
        if i == 0 and not wait_for_port(c["host"], c["port"], proc):
            print("Command node did not start. Last log lines:")
            time.sleep(0.5)
            with LOG_LOCK:
                for _, line in list(LOGS)[-10:]:
                    print("  " + line)
            stop_system(quiet=True)
            return
        time.sleep(0.5)      
    print(f"Started {len(cmds)} processes (1 command node, {len(cmds) - 1} nodes).")
    print("Wait 15-30 s, then use 7 (process IDs), 8 (Gantt chart) or 9 (logs).")

def cmd_stop():
    if not RUN["procs"]:
        print("Nothing is running.")
        return
    stop_system()

def cmd_processes():
    ensure_password()
    limit = ask("How many latest processes to show", 20, int, lambda v: v > 0, "Enter a positive number.")
    try:
        print("\nRegistered nodes:")
        h, rows = query("SELECT node_id, node_name, status, TIMESTAMPDIFF(SECOND, last_heartbeat, NOW()) "
                        "FROM Node ORDER BY node_id")
        print_table(["Node ID", "Name", "Status", "Last heartbeat (s ago)"], rows)
        print("\nRegistered processes (requisitions):")
        h, rows = query("SELECT p.process_id, p.process_name, n.node_name, p.priority, p.status, p.created_at, "
                        "(SELECT COUNT(*) FROM Request r WHERE r.process_id = p.process_id) "
                        "FROM `Process` p JOIN Node n ON n.node_id = p.node_id "
                        "ORDER BY p.process_id DESC LIMIT %s", (limit,))
        print_table(["Process ID", "Name", "Node", "Priority", "Status", "Created", "Lines"], rows)
        pid = input("\nProcess ID for details (Enter to skip): ").strip()
        if pid.isdigit():
            h, rows = query("SELECT rq.request_id, rq.request_type, rq.quantity, rq.status, "
                            "a.quantity_allocated, a.status, sn.node_name "
                            "FROM Request rq LEFT JOIN Allocation a ON a.request_id = rq.request_id "
                            "LEFT JOIN Node sn ON sn.node_id = a.node_id WHERE rq.process_id = %s", (int(pid),))
            print_table(["Request ID", "Resource", "Qty", "Request status", "Allocated", "Alloc status", "Supplier"],
                        rows)
    except Exception as e:
        print(f"Could not read the database: {e}\nStart the system at least once (option 6) and check the password.")
        os.environ.pop("DB_PASSWORD", None)

def render_gantt(jobs, window, width=72):
    t1 = max(j["end"] for j in jobs)
    t0 = max(min(j["start"] for j in jobs), t1 - window)
    jobs = [j for j in jobs if j["end"] > t0]
    span = max(t1 - t0, 0.001)
    scale = width / span
    nodes = sorted({j["node"] for j in jobs})
    lw = max(len(n) for n in nodes)
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600) if span / s <= 8), 7200)

    ruler, labels, t = ["-"] * width, [" "] * (width + 6), 0.0
    while t <= span:
        col = min(width - 1, int(t * scale))
        ruler[col] = "+"
        for k, ch in enumerate(f"{t:g}s"):
            if col + k < len(labels):
                labels[col + k] = ch
        t += step
    print(f"\nGantt chart of local jobs: last {span:.0f}s, t=0 at {time.strftime('%H:%M:%S', time.localtime(t0))}")
    print(" " * (lw + 2) + "".join(labels).rstrip())
    print(f"{'':<{lw}} +{''.join(ruler)}+")
    for node in nodes:
        row, busy = [" "] * width, 0.0
        mine = sorted((j for j in jobs if j["node"] == node), key=lambda j: j["start"])
        for i, j in enumerate(mine):
            s, e = max(j["start"], t0), min(j["end"], t1)
            busy += e - s
            c0 = min(width - 1, int((s - t0) * scale))
            c1 = min(width, max(c0 + 1, int((e - t0) * scale)))
            for c in range(c0, c1):
                row[c] = "#" if i % 2 == 0 else "="
            tag = f"P{j['id']}"
            if c1 - c0 >= len(tag) + 1:
                for k, ch in enumerate(tag):
                    row[c0 + k] = ch
        print(f"{node:<{lw}} |{''.join(row)}| {busy / span * 100:3.0f}% busy")
    print("(P<n> = job ID on that node, # and = alternate between consecutive jobs)\n")
    shown = sorted(jobs, key=lambda j: j["start"])[-25:]
    print_table(["Node", "Job ID", "Job", "Priority", "Start (s)", "End (s)", "Burst (s)"],
                [[j["node"], j["id"], j["name"], j["prio"], f"{max(j['start'], t0) - t0:.1f}",
                  f"{j['end'] - t0:.1f}", f"{j['end'] - j['start']:.1f}"] for j in shown])

def cmd_gantt( ):
    with JOB_LOCK:
        jobs = list(JOBS)
    if not jobs:
        print("No finished jobs recorded yet. Start the system (option 6) and wait for around 15-30s:"
              "factory batches take their 'seconds per batch' to finish.")
        return
    render_gantt(jobs, ask("Show the last how many seconds", 60, float, lambda v:v > 0, "Must be greater than 0."))

def cmd_logs():
    choice = ask("Number of recent lines, or w to watch live (Ctrl+C returns here)", 30, str.lower)
    if choice == "w":
        with LOG_LOCK:
            seen = LOG_SEQ - 20
        print("Watching... press Ctrl+C to return to the menu.")
        try:
            while True:
                with LOG_LOCK:
                    new = [(s, line) for s, line in LOGS if s > seen]
                for s, line in new:
                    print(line)
                    seen = s
                time.sleep(0.3)
        except KeyboardInterrupt:
            print()
        return
    try:
        n = int(choice)
    except ValueError:
        print("Enter a number or w.")
        return
    with LOG_LOCK:
        lines = [line for _, line in list(LOGS)[-n:]]
    print("\n".join(lines) if lines else "No log lines yet.")

ACTIONS = {
    "1": cmd_central, "2": cmd_add_warehouse, "3": cmd_add_factory, "4": cmd_show, "5": cmd_remove,
    "6": cmd_start, "7": cmd_processes, "8": cmd_gantt, "9": cmd_logs, "s": cmd_stop, "d": cmd_demo,
}

def print_menu():
    status = f"RUNNING ({len(RUN['procs'])} processes)" if RUN["procs"] else "stopped"
    print("\n==== OGAS Command Console ====")
    print(f"System: {status} | warehouses: {len(STATE['warehouses'])} | factory entries: {len(STATE['factories'])}")
    print(" 1) Command node settings      6) Start system")
    print(" 2) Add warehouse              7) Registered nodes & process IDs")
    print(" 3) Add factory                8) Gantt chart")
    print(" 4) Show configuration         9) View logs")
    print(" 5) Remove warehouse/factory   s) Stop system")
    print(" d) Load demo configuration    0) Exit")

def main():
    load_config()
    try:
        while True:
            refresh_procs()
            print_menu()
            choice = input("Select: ").strip().lower()
            if choice == "0":
                break
            action = ACTIONS.get(choice)
            if action is None:
                print("Unknown option.")
                continue
            action()
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        stop_system(quiet=True)
        print("Bye.")

if __name__ == "__main__":
    main()