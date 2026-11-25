import React from "react";
import { RefreshCw } from "lucide-react";
import { IS_MOCK, fmtCurrency, fmtNum, fmtVal } from "../api/client";

export default function TopBar({ topology, liveState, apiError, onSwitchPolicy, onSync }) {
  const nodes = topology?.nodes || [];
  const routes = topology?.routes || [];
  const totalNodes = nodes.length || liveState?.nodes?.length || "—";
  const onlineNodes =
    liveState?.system?.nodes_online ??
    (liveState?.nodes ? liveState.nodes.filter((n) => n.status === "ONLINE").length : "—");

  const cpuStr = liveState?.system?.cpu_pct != null ? `${fmtNum(liveState.system.cpu_pct, 1)}%` : "—";
  const memStr = liveState?.system?.mem_pct != null ? `${fmtNum(liveState.system.mem_pct, 1)}%` : "—";
  const threads = fmtVal(liveState?.system?.threads);
  const currency = topology?.meta?.currency || "USD";
  const slv = liveState?.solver || {};
  const policy = liveState?.scheduler?.policy || "PRIORITY";

  const fp = topology?.fingerprint ? String(topology.fingerprint).slice(0, 12) : "—";

  return (
    <header className="h-[46px] min-h-[46px] bg-ctrl-panel border-b border-ctrl-border flex items-center justify-between px-3 gap-3">
      {/* BRAND & TOPOLOGY META */}
      <div className="flex items-center gap-2.5 min-w-[230px]">
        <span className="bg-ctrl-elevated border border-ctrl-strong text-sky-400 font-mono font-bold text-[11px] px-2 py-0.5 tracking-widest">
          SC-CTRL
        </span>
        <div className="flex flex-col">
          <span className="font-semibold text-xs text-slate-100 truncate max-w-[250px]">
            {topology?.meta?.name || "LOADING TOPOLOGY..."}
          </span>
          <span className="font-mono text-[10px] text-slate-500">
            FP: {fp} | SRC: {topology?.source || "—"} | N:{nodes.length} R:{routes.length}
          </span>
        </div>
      </div>

      {/* KPI TELEMETRY STRIP */}
      <div className="flex items-center bg-ctrl-border gap-px border border-ctrl-border">
        <div className="bg-ctrl-root px-2.5 py-1 flex flex-col min-w-[96px]">
          <span className="text-[9px] tracking-wider text-slate-500 font-semibold">API / MODE</span>
          <span
            className={`font-mono text-xs font-semibold ${
              IS_MOCK ? "text-amber-400" : apiError ? "text-red-400" : "text-emerald-400"
            }`}
          >
            {IS_MOCK ? "MOCK (?mock=1)" : apiError ? "API ERROR" : liveState ? "LIVE HTTP" : "INIT"}
          </span>
        </div>

        <div className="bg-ctrl-root px-2.5 py-1 flex flex-col min-w-[88px]">
          <span className="text-[9px] tracking-wider text-slate-500 font-semibold">NODES ONLINE</span>
          <span className="font-mono tabular-nums text-xs font-semibold text-slate-100">
            {onlineNodes} / {totalNodes}
          </span>
        </div>

        <div className="bg-ctrl-root px-2.5 py-1 flex flex-col min-w-[104px]">
          <span className="text-[9px] tracking-wider text-slate-500 font-semibold">CPU / MEM</span>
          <span className="font-mono tabular-nums text-xs font-semibold text-slate-100">
            {cpuStr} / {memStr}
          </span>
        </div>

        <div className="bg-ctrl-root px-2.5 py-1 flex flex-col min-w-[72px]">
          <span className="text-[9px] tracking-wider text-slate-500 font-semibold">THREADS</span>
          <span className="font-mono tabular-nums text-xs font-semibold text-slate-100">{threads}</span>
        </div>

        <div className="bg-ctrl-root px-2.5 py-1 flex flex-col min-w-[116px]">
          <span className="text-[9px] tracking-wider text-slate-500 font-semibold">OBJECTIVE COST</span>
          <span className="font-mono tabular-nums text-xs font-semibold text-sky-400">
            {fmtCurrency(slv.objective_cost, currency)}
          </span>
        </div>

        <div className="bg-ctrl-root px-2.5 py-1 flex flex-col min-w-[82px]">
          <span className="text-[9px] tracking-wider text-slate-500 font-semibold">SOLVE TIME</span>
          <span className="font-mono tabular-nums text-xs font-semibold text-slate-100">
            {slv.solve_ms != null ? `${fmtNum(slv.solve_ms, 0)} ms` : "—"}
          </span>
        </div>
      </div>

      {/* CONTROLS */}
      <div className="flex items-center gap-2">
        <div className="flex items-center bg-ctrl-root border border-ctrl-strong p-0.5 gap-0.5">
          <span className="text-[9px] font-bold tracking-wider text-slate-500 px-1.5">SCHEDULER</span>
          {["PRIORITY", "FCFS"].map((mode) => (
            <button
              key={mode}
              onClick={() => onSwitchPolicy(mode)}
              className={`font-mono text-[10px] font-semibold px-2 py-1 transition-colors ${
                policy === mode
                  ? "bg-ctrl-elevated text-sky-400 shadow-[inset_0_-2px_0_#38bdf8]"
                  : "text-slate-400 hover:bg-ctrl-hover hover:text-slate-100"
              }`}
            >
              {mode}
            </button>
          ))}
        </div>

        <button
          onClick={onSync}
          className="flex items-center gap-1 border border-ctrl-strong bg-ctrl-elevated hover:bg-ctrl-hover text-slate-200 font-mono text-[10px] font-semibold px-2.5 py-1 transition-colors"
          title="Refresh operational lists"
        >
          <RefreshCw className="w-3 h-3" />
          SYNC
        </button>
      </div>
    </header>
  );
}
