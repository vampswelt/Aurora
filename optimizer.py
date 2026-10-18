import logging
import threading

from scipy.optimize import linprog

log = logging.getLogger("optimizer")
COST = {"WAREHOUSE": 1.0, "FACTORY": 2.0}
EPS = 1e-6

class AllocationEngine:
    def __init__(self, max_concurrent=1):
        self._sem = threading.Semaphore(max_concurrent)

    def plan_process(self, requester, reqs, supply):
        """Planner callback for database.allocate_process. All-or-nothing across resources.
        Returns {request_id: [(supplier_node_id, qty), ...]} or None on shortage."""
        pools = {}
        for s in supply:
            avail = float(s["available_quantity"])
            if s["node_id"] != requester and avail > 0:
                pools.setdefault(s["resource_id"], []).append(
                    {"node_id": s["node_id"], "left": avail, "cost": COST[s["node_type"]]})
        plan = {}
        with self._sem:                      
            for r in reqs:
                alloc = self._solve(float(r["quantity"]), pools.get(r["resource_id"], []))
                if alloc is None:
                    log.info("shortage for %s x%s", r["resource_name"], r["quantity"])
                    return None
                plan[r["request_id"]] = alloc
        return plan

    @staticmethod
    def _solve(qty, pool):
        if not pool or sum(p["left"] for p in pool) < qty - EPS:
            return None
        big_m = max(p["cost"] for p in pool) + 1
        res = linprog(c=[p["cost"] - big_m for p in pool],
                      A_ub=[[1.0] * len(pool)], b_ub=[qty],
                      bounds=[(0, p["left"]) for p in pool], method="highs")
        if not res.success:
            return None
        out = []
        for p, x in zip(pool, res.x):
            x = round(float(min(x, p["left"])), 2)   
            if x > 0:
                p["left"] -= x               
                out.append((p["node_id"], x))
        if sum(x for _, x in out) < qty - 0.01:
            return None
        return out

if __name__ == "__main__":      
    eng = AllocationEngine()
    reqs = [{"request_id": 1, "resource_id": 1, "resource_name": "STEEL", "quantity": 120}]
    sup = [{"resource_id": 1, "node_id": 2, "available_quantity": 100, "node_type": "WAREHOUSE"},
           {"resource_id": 1, "node_id": 3, "available_quantity": 500, "node_type": "FACTORY"}]
    print(eng.plan_process(requester=9, reqs=reqs, supply=sup))  