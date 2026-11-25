"""
network_optimizer.py : multi-echelon, multi-commodity minimum-cost flow, solved with SciPy HiGHS.

The LP matrices are built entirely from the Topology object (topology_loader.py): nothing about
suppliers, hubs, routes, resources or tariffs is hardcoded here, so a different JSON file just works.

Decision variables (units per day)
  f[a, r]  flow of resource r on arc a, where an arc is one (route, transport mode) pair
  s[n, r]  supply drawn from node n (bounded by its capacity_per_day)
  u[n, r]  unmet demand at node n (bounded by its demand)

  minimise   sum (tariff + time_value_per_day * transit_days) * f
           + sum unit_cost * s
           + shortage_penalty * sum u
  subject to s[n,r] + inflow[n,r] - outflow[n,r] + u[n,r] = demand[n,r]     (flow conservation)
             sum over r of f[a,r] <= capacity_per_day(a)                    (shared arc capacity)
             0 <= s <= supply capacity,  0 <= u <= demand,  f >= 0

Disruptions remove arcs and supply: a disrupted node loses all its arcs and its supply (its own
demand then counts as unmet), a disrupted route loses all its modes, and a disabled mode (for
example "air") is removed network-wide. Re-solving is just calling solve_network again.
"""
import time

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

EPS = 1e-6


def _round(x):
    return round(float(x), 2)


def solve_network(topo, disabled_nodes=(), disabled_routes=(), disabled_modes=()):
    """Solve one daily plan. Returns a dict (see keys at the bottom). Never raises on a feasible model."""
    t0 = time.perf_counter()
    disabled_nodes, disabled_routes, disabled_modes = set(disabled_nodes), set(disabled_routes), set(disabled_modes)
    penalty = topo.solver["shortage_penalty"]
    time_value = topo.solver["time_value_per_day"]

    active = [r for r in topo.resources if any(n.demand.get(r, 0) > 0 for n in topo.nodes.values())]
    demand_total = {r: _round(sum(n.demand.get(r, 0) for n in topo.nodes.values())) for r in active}
    if not active:
        return _result("OPTIMAL", 0, 0, 0, 0, {}, {}, {}, demand_total, {}, 0, 0, 0, t0, "no demand in topology")
    rindex = {r: i for i, r in enumerate(active)}
    n_res = len(active)

    arcs = []                                   # (route_id, mode, src, dst, cost per unit, capacity)
    for route in topo.routes.values():
        if route.id in disabled_routes or route.src in disabled_nodes or route.dst in disabled_nodes:
            continue
        for mode_name, m in route.modes.items():
            if mode_name not in disabled_modes:
                arcs.append((route.id, mode_name, route.src, route.dst,
                             m.cost_per_unit + time_value * m.transit_days, m.capacity_per_day))
    supply_vars = [(n.id, res, spec["capacity_per_day"], spec["unit_cost"])
                   for n in topo.nodes.values() if n.id not in disabled_nodes
                   for res, spec in n.supply.items() if res in rindex and spec["capacity_per_day"] > 0]
    unmet_vars = [(n.id, res, n.demand[res]) for n in topo.nodes.values()
                  for res in n.demand if res in rindex and n.demand[res] > 0]

    n_flow, n_sup = len(arcs) * n_res, len(supply_vars)
    n_var = n_flow + n_sup + len(unmet_vars)
    cost = np.zeros(n_var)
    lower, upper = np.zeros(n_var), np.full(n_var, np.inf)

    rows, b_eq = {}, []
    eq_r, eq_c, eq_v = [], [], []

    def row(node_id, res):
        key = (node_id, res)
        if key not in rows:
            rows[key] = len(rows)
            b_eq.append(float(topo.nodes[node_id].demand.get(res, 0.0)))
        return rows[key]

    ub_r, ub_c, b_ub = [], [], []
    for i, (_, _, src, dst, unit_cost, cap) in enumerate(arcs):
        for res, k in rindex.items():
            col = i * n_res + k
            cost[col] = unit_cost
            eq_r += [row(dst, res), row(src, res)]
            eq_c += [col, col]
            eq_v += [1.0, -1.0]
            ub_r.append(i)
            ub_c.append(col)
        b_ub.append(cap)
    for j, (node_id, res, cap, unit_cost) in enumerate(supply_vars):
        col = n_flow + j
        cost[col], upper[col] = unit_cost, cap
        eq_r.append(row(node_id, res))
        eq_c.append(col)
        eq_v.append(1.0)
    for j, (node_id, res, dem) in enumerate(unmet_vars):
        col = n_flow + n_sup + j
        cost[col], upper[col] = penalty, dem
        eq_r.append(row(node_id, res))
        eq_c.append(col)
        eq_v.append(1.0)

    a_eq = coo_matrix((eq_v, (eq_r, eq_c)), shape=(len(rows), n_var)).tocsr()
    kwargs = {}
    if arcs:
        kwargs = {"A_ub": coo_matrix((np.ones(len(ub_r)), (ub_r, ub_c)), shape=(len(arcs), n_var)).tocsr(),
                  "b_ub": np.array(b_ub)}
    res = linprog(cost, A_eq=a_eq, b_eq=np.array(b_eq), bounds=np.column_stack([lower, upper]),
                  method="highs", **kwargs)
    if not res.success:
        return _result("ERROR", 0, 0, 0, 0, {}, {}, {}, demand_total, {}, n_var, len(rows) + len(arcs), 0, t0,
                       str(res.message))

    x = res.x
    flows, mode_split = {}, {}
    for i, (route_id, mode_name, *_rest) in enumerate(arcs):
        for res_id, k in rindex.items():
            v = x[i * n_res + k]
            if v > EPS:
                flows.setdefault(route_id, {}).setdefault(mode_name, {})[res_id] = _round(v)
                mode_split[mode_name] = _round(mode_split.get(mode_name, 0) + v)
    supply, unmet = {}, {}
    for j, (node_id, res_id, _cap, _c) in enumerate(supply_vars):
        if x[n_flow + j] > EPS:
            supply.setdefault(node_id, {})[res_id] = _round(x[n_flow + j])
    for j, (node_id, res_id, _dem) in enumerate(unmet_vars):
        if x[n_flow + n_sup + j] > EPS:
            unmet.setdefault(node_id, {})[res_id] = _round(x[n_flow + n_sup + j])
    return _result("OPTIMAL", float(res.fun), float(cost[:n_flow] @ x[:n_flow]),
                   float(cost[n_flow:n_flow + n_sup] @ x[n_flow:n_flow + n_sup]),
                   float(cost[n_flow + n_sup:] @ x[n_flow + n_sup:]),
                   flows, supply, unmet, demand_total, mode_split, n_var, len(rows) + len(arcs), len(arcs), t0, "")


def _result(status, total, shipping, supply_cost, shortage, flows, supply, unmet, demand_total, mode_split,
            n_var, n_con, n_arcs, t0, message):
    unmet_total = {}
    for per_node in unmet.values():
        for res_id, v in per_node.items():
            unmet_total[res_id] = _round(unmet_total.get(res_id, 0) + v)
    demand_sum = sum(demand_total.values())
    return {
        "status": status, "message": message,
        "objective_cost": _round(total), "shipping_cost": _round(shipping),
        "supply_cost": _round(supply_cost), "shortage_cost": _round(shortage),
        "flows": flows, "supply": supply, "unmet_by_node": unmet, "unmet": unmet_total,
        "demand_total": demand_total,
        "fulfillment_pct": _round(100 * (1 - sum(unmet_total.values()) / demand_sum)) if demand_sum else 100.0,
        "mode_split": mode_split, "variables": n_var, "constraints": n_con, "arcs": n_arcs,
        "solve_ms": _round((time.perf_counter() - t0) * 1000),
    }