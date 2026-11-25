import React from "react";
import { fmtCurrency, fmtNum, fmtVal } from "../api/client";

function KV({ label, value, valueClass = "text-slate-100" }) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] tracking-wider text-slate-500 uppercase">{label}</span>
      <span className={`font-mono tabular-nums text-xs font-semibold ${valueClass}`}>{value}</span>
    </div>
  );
}

export default function TelemetryRail({ topology, liveState }) {
  const currency = topology?.meta?.currency || "USD";
  const slv = liveState?.solver || {};
  const sch = liveState?.scheduler || {};
  const db = liveState?.database || {};
  const sys = liveState?.system || {};

  const totalNodes = topology?.nodes?.length ?? liveState?.nodes?.length ?? "—";
  const onlineNodes =
    sys.nodes_online ?? (liveState?.nodes ? liveState.nodes.filter((n) => n.status === "ONLINE").length : "—");

  const slvStatus = slv.status || "NOT_RUN";
  const slvBadgeClass =
    slvStatus === "OPTIMAL"
      ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"
      : slvStatus === "INFEASIBLE"
      ? "text-red-400 border-red-500/40 bg-red-500/10"
      : "text-slate-400 border-ctrl-strong bg-ctrl-elevated";

  const unmetEntries = slv.unmet ? Object.entries(slv.unmet).filter(([, v]) => Number(v) > 0) : [];

  return (
    <aside className="bg-ctrl-root flex flex-col gap-px min-h-0 overflow-y-auto">
      {/* LP OPTIMIZER */}
      <section className="bg-ctrl-panel p-2.5 flex flex-col gap-2">
        <div className="flex items-center justify-between border-b border-ctrl-border pb-1.5">
          <span className="text-[10px] font-bold tracking-wider text-slate-400">LP OPTIMIZER (SCIPY)</span>
          <span className={`font-mono text-[9px] font-bold px-1.5 py-0.5 border ${slvBadgeClass}`}>
            {slvStatus}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-x-2.5 gap-y-1.5">
          <KV label="Total Objective" value={fmtCurrency(slv.objective_cost, currency)} valueClass="text-sky-400" />
          <KV label="Shipping Cost" value={fmtCurrency(slv.shipping_cost, currency)} />
          <KV
            label="Shortage Penalty"
            value={fmtCurrency(slv.shortage_cost, currency)}
            valueClass={Number(slv.shortage_cost) > 0 ? "text-red-400" : "text-slate-100"}
          />
          <KV label="Solve Latency" value={slv.solve_ms != null ? `${fmtNum(slv.solve_ms, 0)} ms` : "—"} />
          <KV label="Trigger Reason" value={fmtVal(slv.reason)} />
          <KV label="Computed At" value={slv.computed_at ? String(slv.computed_at).slice(11, 19) : "—"} />
        </div>

        <div className="border-t border-ctrl-border pt-1.5">
          <div className="text-[9px] font-bold tracking-wider text-slate-500 mb-1">UNMET DEMAND</div>
          {unmetEntries.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {unmetEntries.map(([res, qty]) => (
                <span
                  key={res}
                  className="font-mono text-[9px] font-bold px-1.5 py-0.5 border border-red-500/40 bg-red-500/15 text-red-400"
                >
                  {res}: {fmtNum(qty, 0)}
                </span>
              ))}
            </div>
          ) : (
            <span className="text-slate-500 text-[11px]">Zero unmet demand</span>
          )}
        </div>
      </section>

      {/* TASK SCHEDULER */}
      <section className="bg-ctrl-panel p-2.5 flex flex-col gap-2">
        <div className="flex items-center justify-between border-b border-ctrl-border pb-1.5">
          <span className="text-[10px] font-bold tracking-wider text-slate-400">TASK SCHEDULER</span>
          <span className="font-mono text-[9px] font-bold px-1.5 py-0.5 border border-sky-500/35 bg-sky-500/10 text-sky-400">
            {sch.policy || "PRIORITY"}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-x-2.5 gap-y-1.5">
          <KV label="Queue Depth" value={fmtVal(sch.queue_depth)} />
          <KV label="Worker Threads" value={fmtVal(sch.workers)} />
          <KV label="Aging Interval" value={sch.aging_interval_s != null ? `${sch.aging_interval_s}s` : "—"} />
          <KV label="Dispatched" value={fmtVal(sch.dispatched)} />
        </div>
      </section>

      {/* MYSQL LEDGER */}
      <section className="bg-ctrl-panel p-2.5 flex flex-col gap-2">
        <div className="flex items-center justify-between border-b border-ctrl-border pb-1.5">
          <span className="text-[10px] font-bold tracking-wider text-slate-400">MYSQL 3NF LEDGER</span>
          <span className="font-mono text-[9px] font-bold px-1.5 py-0.5 border border-emerald-500/35 bg-emerald-500/10 text-emerald-400">
            INNODB
          </span>
        </div>

        <div className="grid grid-cols-2 gap-x-2.5 gap-y-1.5">
          <KV label="Transactions" value={fmtVal(db.transactions)} />
          <KV
            label="Deadlock Retries"
            value={fmtVal(db.deadlock_retries)}
            valueClass={Number(db.deadlock_retries) > 0 ? "text-amber-400" : "text-slate-100"}
          />
          <KV
            label="TX Failures"
            value={fmtVal(db.failures)}
            valueClass={Number(db.failures) > 0 ? "text-red-400" : "text-slate-100"}
          />
          <KV label="Avg TX Latency" value={db.avg_tx_ms != null ? `${fmtNum(db.avg_tx_ms, 2)} ms` : "—"} />
          <KV label="Write Slots" value={fmtVal(db.write_slots)} />
        </div>
      </section>

      {/* COMMAND CENTRE HOST */}
      <section className="bg-ctrl-panel p-2.5 flex flex-col gap-2 flex-1">
        <div className="flex items-center justify-between border-b border-ctrl-border pb-1.5">
          <span className="text-[10px] font-bold tracking-wider text-slate-400">COMMAND CENTRE HOST</span>
          <span className="font-mono text-[10px] text-slate-500">
            {liveState?.time ? String(liveState.time).slice(11, 19) : "—"}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-x-2.5 gap-y-1.5">
          <KV label="CPU Utilization" value={sys.cpu_pct != null ? `${fmtNum(sys.cpu_pct, 1)}%` : "—"} />
          <KV label="Memory Usage" value={sys.mem_pct != null ? `${fmtNum(sys.mem_pct, 1)}%` : "—"} />
          <KV label="Active Threads" value={fmtVal(sys.threads)} />
          <KV label="Online Nodes" value={`${onlineNodes} / ${totalNodes}`} />
        </div>
      </section>
    </aside>
  );
}
