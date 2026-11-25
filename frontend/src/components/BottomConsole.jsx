import React, { useMemo, useState } from "react";

export default function BottomConsole({
  events = [],
  queue = [],
  allocations = [],
  jobs = [],
  onSelectEntity
}) {
  const [activeTab, setActiveTab] = useState("events");

  const { minT, spanT } = useMemo(() => {
    if (!jobs || jobs.length === 0) return { minT: 0, spanT: 1 };
    let minVal = Infinity;
    let maxVal = -Infinity;
    for (const j of jobs) {
      const s = Number(j.start);
      const e = Number(j.end);
      if (Number.isFinite(s) && s < minVal) minVal = s;
      if (Number.isFinite(e) && e > maxVal) maxVal = e;
    }
    if (!Number.isFinite(minVal) || !Number.isFinite(maxVal) || maxVal <= minVal) {
      return { minT: 0, spanT: 1 };
    }
    return { minT: minVal, spanT: maxVal - minVal };
  }, [jobs]);

  const tabs = [
    { id: "events", label: "EVENT STREAM", count: events.length },
    { id: "queue", label: "PROCESS QUEUE", count: queue.length },
    { id: "allocations", label: "ALLOCATIONS", count: allocations.length },
    { id: "gantt", label: "NODE GANTT", count: jobs.length }
  ];

  return (
    <div className="h-48 shrink-0 bg-ctrl-panel border-t border-ctrl-border flex flex-col">
      <div className="flex items-center border-b border-ctrl-border px-3 bg-ctrl-bg/60 shrink-0">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-1.5 px-3 py-2 font-mono text-[10px] font-semibold border-b-2 transition-colors ${
                isActive
                  ? "border-sky-400 text-sky-300 bg-ctrl-panel"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              <span>{tab.label}</span>
              <span className="px-1.5 py-0.5 bg-slate-800 text-slate-300 text-[9px] rounded-sm">
                {tab.count}
              </span>
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-2 font-mono text-[11px]">
        {activeTab === "events" && (
          <div className="flex flex-col">
            {events.length === 0 ? (
              <div className="text-slate-500 font-mono text-xs p-2">
                No system events recorded.
              </div>
            ) : (
              events.map((ev, idx) => (
                <div
                  key={ev.id || idx}
                  className="flex items-center gap-3 py-1 border-b border-ctrl-border text-slate-300"
                >
                  <span className="text-slate-500 text-[10px] w-20 shrink-0">
                    {ev.timestamp || ev.time || "—"}
                  </span>
                  <span
                    className={`text-[9px] px-1.5 py-0.5 border shrink-0 ${
                      ev.severity === "ERROR" || ev.level === "CRITICAL"
                        ? "border-red-500/40 text-red-400 bg-red-500/10"
                        : ev.severity === "WARN"
                        ? "border-amber-500/40 text-amber-300 bg-amber-500/10"
                        : "border-sky-500/40 text-sky-300 bg-sky-500/10"
                    }`}
                  >
                    {ev.severity || ev.level || "INFO"}
                  </span>
                  <span className="truncate">{ev.message || ev.msg || JSON.stringify(ev)}</span>
                </div>
              ))
            )}
          </div>
        )}

        {activeTab === "queue" && (
          <div className="flex flex-col">
            {queue.length === 0 ? (
              <div className="text-slate-500 font-mono text-xs p-2">
                Process queue is empty.
              </div>
            ) : (
              queue.map((item, idx) => (
                <div
                  key={item.job_id || item.id || idx}
                  className="flex items-center justify-between py-1 border-b border-ctrl-border text-slate-300"
                >
                  <div className="flex items-center gap-2.5 truncate">
                    <span className="text-sky-400 font-semibold">
                      {item.job_id || item.id || `JOB-${idx + 1}`}
                    </span>
                    <span className="text-slate-400">{item.node || item.node_id || "—"}</span>
                    <span className="truncate">{item.name || item.task || item.type || "—"}</span>
                  </div>
                  <div className="flex items-center gap-3 text-[10px] shrink-0">
                    <span className="text-amber-300">P{item.priority ?? "-"}</span>
                    <span className="text-emerald-400">{item.status || "QUEUED"}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {activeTab === "allocations" && (
          <table className="w-full text-left border-collapse font-mono text-[11px]">
            <thead>
              <tr className="border-b border-ctrl-border text-slate-400 text-[10px]">
                <th className="py-1 px-2">ROUTE</th>
                <th className="py-1 px-2">MODE</th>
                <th className="py-1 px-2">RESOURCE</th>
                <th className="py-1 px-2">QTY</th>
                <th className="py-1 px-2">STATUS</th>
                <th className="py-1 px-2">ALLOCATED AT</th>
              </tr>
            </thead>
            <tbody>
              {allocations.length === 0 ? (
                <tr>
                  <td colSpan={6} className="text-slate-500 text-xs p-2">
                    No active allocations reported.
                  </td>
                </tr>
              ) : (
                allocations.map((a, idx) => (
                  <tr
                    key={a.id || idx}
                    onClick={() => a.route_id && onSelectEntity?.("route", a.route_id)}
                    className="border-b border-ctrl-border text-slate-300 hover:bg-ctrl-hover/40 cursor-pointer"
                  >
                    <td className="py-1 px-2 text-teal-400">
                      {a.route_id || `${a.from || ""} → ${a.to || ""}`}
                    </td>
                    <td className="py-1 px-2 uppercase text-slate-400">{a.mode || "—"}</td>
                    <td className="py-1 px-2">{a.resource || a.item || "—"}</td>
                    <td className="py-1 px-2 text-slate-200">{a.quantity ?? a.flow ?? "—"}</td>
                    <td className="py-1 px-2">{a.status}</td>
                    <td className="py-1 px-2 text-slate-400">{a.allocated_at || "—"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}

        {activeTab === "gantt" && (
          <div className="flex flex-col">
            {jobs.length === 0 ? (
              <div className="text-slate-500 font-mono text-xs p-2">
                No node-local job windows reported on /api/jobs.
              </div>
            ) : (
              jobs.map((j) => {
                const leftPct = ((Number(j.start) - minT) / spanT) * 100;
                const widthPct = Math.max(5, ((Number(j.end) - Number(j.start)) / spanT) * 100);
                const prio = Number(j.priority ?? 3);
                const barTone =
                  prio === 1
                    ? "bg-amber-500/25 border-amber-400 text-amber-100"
                    : prio <= 3
                    ? "bg-sky-500/25 border-sky-400 text-sky-100"
                    : "bg-violet-500/25 border-violet-400 text-violet-100";

                return (
                  <div
                    key={j.job_id}
                    className="grid grid-cols-[132px_1fr] items-center gap-2.5 py-1.5 border-b border-ctrl-border"
                  >
                    <span className="font-mono text-[11px] text-slate-300 truncate">
                      {j.node} · <span className="text-sky-400 font-semibold">P{j.priority}</span>
                    </span>
                    <div className="relative h-6 bg-ctrl-root border border-ctrl-border">
                      <div
                        style={{ left: `${leftPct.toFixed(1)}%`, width: `${widthPct.toFixed(1)}%` }}
                        title={`${j.name} (${j.job_id}) · ${j.node} · P${j.priority}`}
                        className={`absolute top-0.5 bottom-0.5 border font-mono text-[10px] leading-none flex items-center px-1.5 truncate ${barTone}`}
                      >
                        <span className="truncate">
                          {j.name} ({j.job_id})
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
}