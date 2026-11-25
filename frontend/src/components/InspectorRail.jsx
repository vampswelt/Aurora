import React, { useState } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { fmtNum, fmtVal } from "../api/client";

function KVMap({ obj, emptyLabel = "None" }) {
  if (!obj || typeof obj !== "object" || Object.keys(obj).length === 0) {
    return <span className="text-slate-500 text-[11px]">{emptyLabel}</span>;
  }
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {Object.entries(obj).map(([k, v]) => {
        const valStr =
          typeof v === "object" && v !== null
            ? Object.entries(v)
                .map(([subK, subV]) => `${subK}: ${subV}`)
                .join(" · ")
            : fmtNum(v, 1);
        return (
          <div key={k} className="flex flex-col">
            <span className="text-[9px] text-slate-500">{k}</span>
            <span className="font-mono tabular-nums text-[11px] font-semibold text-slate-100">{valStr}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function InspectorRail({
  topology,
  liveState,
  selected,
  onSelectEntity,
  onTriggerDisrupt,
  searchFilter
}) {
  const [dirTab, setDirTab] = useState("nodes");

  const nodes = topology?.nodes || [];
  const routes = topology?.routes || [];
  const liveNodeMap = new Map((liveState?.nodes || []).map((n) => [n.id, n]));
  const liveRouteMap = new Map((liveState?.routes || []).map((r) => [r.id, r]));

  const q = searchFilter.trim().toLowerCase();
  const filteredNodes = nodes.filter(
    (n) => !q || n.id.toLowerCase().includes(q) || n.name.toLowerCase().includes(q) || n.role.toLowerCase().includes(q)
  );
  const filteredRoutes = routes.filter(
    (r) => !q || r.id.toLowerCase().includes(q) || r.from.toLowerCase().includes(q) || r.to.toLowerCase().includes(q)
  );

  // Selected entity data
  const selNode = selected?.type === "node" ? nodes.find((n) => n.id === selected.id) : null;
  const selNodeLive = selNode ? liveNodeMap.get(selNode.id) || {} : null;

  const selRoute = selected?.type === "route" ? routes.find((r) => r.id === selected.id) : null;
  const selRouteLive = selRoute ? liveRouteMap.get(selRoute.id) || {} : null;

  const nodeStatus = selNodeLive?.status || "ONLINE";
  const nodeDisrupted = Boolean(selNodeLive?.disrupted) || nodeStatus === "OFFLINE";

  const routeStatus = selRouteLive?.status || selRoute?.status || "ACTIVE";
  const routeDisrupted = routeStatus === "DISRUPTED";

  return (
    <aside className="bg-ctrl-root flex flex-col gap-px min-h-0 overflow-hidden">
      {/* ENTITY INSPECTOR */}
      <section className="bg-ctrl-panel p-2.5 flex flex-col gap-2 flex-[1_1_56%] min-h-0">
        <div className="flex items-center justify-between border-b border-ctrl-border pb-1.5">
          <span className="text-[10px] font-bold tracking-wider text-slate-400">
            {selNode ? `NODE // ${selNode.id}` : selRoute ? `CORRIDOR // ${selRoute.id}` : "ENTITY INSPECTOR"}
          </span>
          {selNode && (
            <span
              className={`font-mono text-[9px] font-bold px-1.5 py-0.5 border ${
                nodeDisrupted
                  ? "text-red-400 border-red-500/40 bg-red-500/15"
                  : nodeStatus === "DEGRADED"
                  ? "text-amber-400 border-amber-500/40 bg-amber-500/15"
                  : "text-emerald-400 border-emerald-500/35 bg-emerald-500/10"
              }`}
            >
              {nodeStatus}
            </span>
          )}
          {selRoute && (
            <span
              className={`font-mono text-[9px] font-bold px-1.5 py-0.5 border ${
                routeDisrupted
                  ? "text-red-400 border-red-500/40 bg-red-500/15"
                  : "text-emerald-400 border-emerald-500/35 bg-emerald-500/10"
              }`}
            >
              {routeStatus}
            </span>
          )}
          {!selNode && !selRoute && (
            <span className="font-mono text-[9px] font-bold px-1.5 py-0.5 border border-ctrl-strong bg-ctrl-elevated text-slate-400">
              IDLE
            </span>
          )}
        </div>

        <div className="flex-1 overflow-y-auto flex flex-col gap-2 pr-0.5">
          {!selNode && !selRoute && (
            <div className="text-slate-500 p-4 border border-dashed border-ctrl-strong text-center text-[11px]">
              Select any <strong className="text-slate-300">Node</strong> or{" "}
              <strong className="text-slate-300">Route</strong> on the map or directory below to inspect capacities,
              live flow, and trigger disruption simulations.
            </div>
          )}

          {selNode && (
            <>
              <div className="bg-ctrl-elevated border border-ctrl-border p-2 flex flex-col gap-2">
                <div className="text-[13px] font-bold text-slate-100">{selNode.name}</div>
                <div className="grid grid-cols-2 gap-1.5">
                  <div>
                    <div className="text-[9px] text-slate-500">ROLE</div>
                    <div className="font-mono text-[11px] font-semibold">{selNode.role}</div>
                  </div>
                  <div>
                    <div className="text-[9px] text-slate-500">COORDINATES</div>
                    <div className="font-mono text-[11px] font-semibold">
                      {selNode.lat.toFixed(2)}, {selNode.lon.toFixed(2)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] text-slate-500">HEARTBEAT AGE</div>
                    <div className="font-mono text-[11px] font-semibold">
                      {selNodeLive?.heartbeat_age_s != null ? `${selNodeLive.heartbeat_age_s}s` : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] text-slate-500">ACTIVE JOBS</div>
                    <div className="font-mono text-[11px] font-semibold">{fmtVal(selNodeLive?.active_jobs)}</div>
                  </div>
                  <div>
                    <div className="text-[9px] text-slate-500">CPU / MEMORY</div>
                    <div className="font-mono text-[11px] font-semibold">
                      {selNodeLive?.cpu_pct != null ? `${selNodeLive.cpu_pct}%` : "—"} /{" "}
                      {selNodeLive?.memory_pct != null ? `${selNodeLive.memory_pct}%` : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] text-slate-500">DISRUPTED FLAG</div>
                    <div className="font-mono text-[11px] font-semibold">{nodeDisrupted ? "TRUE" : "FALSE"}</div>
                  </div>
                </div>

                <button
                  onClick={() => onTriggerDisrupt("node", selNode.id, nodeDisrupted ? "ACTIVE" : "DISRUPTED")}
                  className={`w-full py-1.5 px-2.5 font-mono text-[11px] font-bold tracking-wider flex items-center justify-center gap-1.5 border transition-colors ${
                    nodeDisrupted
                      ? "border-emerald-500 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400"
                      : "border-red-500 bg-red-500/15 hover:bg-red-500/25 text-red-400"
                  }`}
                >
                  {nodeDisrupted ? <RotateCcw className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
                  {nodeDisrupted ? "RESTORE NODE OPERATION" : "SIMULATE NODE DISRUPTION"}
                </button>
              </div>

              <div className="bg-ctrl-elevated border border-ctrl-border p-2 flex flex-col gap-1">
                <div className="text-[9px] font-bold tracking-wider text-slate-500">LIVE INVENTORY</div>
                <KVMap obj={selNodeLive?.inventory || selNode.stock} emptyLabel="No inventory telemetry" />
              </div>

              <div className="bg-ctrl-elevated border border-ctrl-border p-2 flex flex-col gap-1">
                <div className="text-[9px] font-bold tracking-wider text-slate-500">SUPPLY CAPACITY / DAY</div>
                <KVMap obj={selNode.supply} emptyLabel="Not a primary supplier" />
              </div>

              <div className="bg-ctrl-elevated border border-ctrl-border p-2 flex flex-col gap-1">
                <div className="text-[9px] font-bold tracking-wider text-slate-500">DEMAND &amp; STORAGE</div>
                <KVMap obj={selNode.demand} emptyLabel="No sink demand" />
                <KVMap obj={selNode.storage} emptyLabel="No storage limits" />
              </div>
            </>
          )}

          {selRoute && (
            <>
              <div className="bg-ctrl-elevated border border-ctrl-border p-2 flex flex-col gap-2">
                <div className="text-[13px] font-bold text-slate-100 font-mono">
                  {selRoute.from} &rarr; {selRoute.to}
                </div>
                <button
                  onClick={() => onTriggerDisrupt("route", selRoute.id, routeDisrupted ? "ACTIVE" : "DISRUPTED")}
                  className={`w-full py-1.5 px-2.5 font-mono text-[11px] font-bold tracking-wider flex items-center justify-center gap-1.5 border transition-colors ${
                    routeDisrupted
                      ? "border-emerald-500 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400"
                      : "border-red-500 bg-red-500/15 hover:bg-red-500/25 text-red-400"
                  }`}
                >
                  {routeDisrupted ? <RotateCcw className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
                  {routeDisrupted ? "RESTORE CORRIDOR" : "DISRUPT CORRIDOR & RE-SOLVE"}
                </button>
              </div>

              {Object.entries(selRoute.modes || {}).map(([mode, spec]) => {
                const flowObj = selRouteLive?.flows?.[mode] || {};
                const flowSummary = Object.keys(flowObj).length
                  ? Object.entries(flowObj)
                      .map(([res, qVal]) => `${res}: ${fmtNum(qVal, 0)}`)
                      .join(", ")
                  : "0";
                return (
                  <div key={mode} className="bg-ctrl-elevated border border-ctrl-border p-2 flex flex-col gap-1.5">
                    <div className="flex items-center justify-between border-b border-ctrl-border pb-1 font-mono text-[10px]">
                      <span className="font-bold text-sky-400">MODE: {mode.toUpperCase()}</span>
                      <span className="text-slate-300">FLOW: {flowSummary}</span>
                    </div>
                    <div className="grid grid-cols-3 gap-1.5">
                      <div>
                        <div className="text-[9px] text-slate-500">COST/UNIT</div>
                        <div className="font-mono text-[11px] font-semibold">{fmtNum(spec.cost_per_unit, 2)}</div>
                      </div>
                      <div>
                        <div className="text-[9px] text-slate-500">TRANSIT</div>
                        <div className="font-mono text-[11px] font-semibold">{fmtNum(spec.transit_days, 1)} d</div>
                      </div>
                      <div>
                        <div className="text-[9px] text-slate-500">CAP/DAY</div>
                        <div className="font-mono text-[11px] font-semibold">{fmtNum(spec.capacity_per_day, 0)}</div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </>
          )}
        </div>
      </section>

      {/* NETWORK DIRECTORY */}
      <section className="bg-ctrl-panel p-2.5 flex flex-col gap-2 flex-[1_1_44%] min-h-[175px]">
        <div className="flex items-center justify-between border-b border-ctrl-border pb-1">
          <span className="text-[10px] font-bold tracking-wider text-slate-400">NETWORK DIRECTORY</span>
          <div className="flex gap-1">
            <button
              onClick={() => setDirTab("nodes")}
              className={`font-mono text-[10px] font-semibold px-2 py-0.5 ${
                dirTab === "nodes"
                  ? "bg-ctrl-elevated text-sky-400 shadow-[inset_0_-2px_0_#38bdf8]"
                  : "text-slate-400 hover:text-slate-100"
              }`}
            >
              NODES ({filteredNodes.length})
            </button>
            <button
              onClick={() => setDirTab("routes")}
              className={`font-mono text-[10px] font-semibold px-2 py-0.5 ${
                dirTab === "routes"
                  ? "bg-ctrl-elevated text-sky-400 shadow-[inset_0_-2px_0_#38bdf8]"
                  : "text-slate-400 hover:text-slate-100"
              }`}
            >
              ROUTES ({filteredRoutes.length})
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto flex flex-col gap-1 pr-0.5">
          {dirTab === "nodes"
            ? filteredNodes.slice(0, 250).map((n) => {
                const ln = liveNodeMap.get(n.id);
                const st = ln?.disrupted ? "DISRUPTED" : ln?.status || "ONLINE";
                const isSel = selected?.type === "node" && selected?.id === n.id;
                const stCls =
                  st === "ONLINE" ? "text-emerald-400" : st === "DEGRADED" ? "text-amber-400" : "text-red-400";
                return (
                  <button
                    key={n.id}
                    onClick={() => onSelectEntity("node", n.id)}
                    className={`flex items-center justify-between px-2 py-1 bg-ctrl-root border text-left font-mono text-[10px] transition-colors ${
                      isSel ? "border-sky-400 bg-ctrl-hover" : "border-ctrl-border hover:bg-ctrl-hover"
                    }`}
                  >
                    <span className="truncate pr-2">
                      <strong className="text-slate-100">{n.id}</strong> · {n.name}
                    </span>
                    <span className={stCls}>{st}</span>
                  </button>
                );
              })
            : filteredRoutes.slice(0, 250).map((r) => {
                const lr = liveRouteMap.get(r.id);
                const st = lr?.status || r.status || "ACTIVE";
                const isSel = selected?.type === "route" && selected?.id === r.id;
                const stCls = st === "ACTIVE" ? "text-emerald-400" : "text-red-400";
                return (
                  <button
                    key={r.id}
                    onClick={() => onSelectEntity("route", r.id)}
                    className={`flex items-center justify-between px-2 py-1 bg-ctrl-root border text-left font-mono text-[10px] transition-colors ${
                      isSel ? "border-sky-400 bg-ctrl-hover" : "border-ctrl-border hover:bg-ctrl-hover"
                    }`}
                  >
                    <span className="truncate pr-2">
                      <strong className="text-slate-100">{r.id}</strong> ({r.from}&rarr;{r.to})
                    </span>
                    <span className={stCls}>{st}</span>
                  </button>
                );
              })}
        </div>
      </section>
    </aside>
  );
}
