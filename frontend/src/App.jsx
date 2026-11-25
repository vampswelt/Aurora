import React, { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./api/client";
import TopBar from "./components/TopBar";
import TelemetryRail from "./components/TelemetryRail";
import LogisticsMap from "./components/LogisticsMap";
import BottomConsole from "./components/BottomConsole";
import InspectorRail from "./components/InspectorRail";

export default function App() {
  const [topology, setTopology] = useState(null);
  const [liveState, setLiveState] = useState(null);
  const [processes, setProcesses] = useState([]);
  const [allocations, setAllocations] = useState([]);
  const [events, setEvents] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState(null);
  const [searchFilter, setSearchFilter] = useState("");
  const [apiError, setApiError] = useState(false);
  const [toasts, setToasts] = useState([]);

  const pushToast = useCallback((msg, kind = "info") => {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts((prev) => [...prev, { id, msg, kind }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4200);
  }, []);

  const pollState = useCallback(async () => {
    try {
      const data = await apiFetch("/api/state");
      setLiveState(data);
      setApiError(false);
    } catch {
      setApiError(true);
    }
  }, []);

  const refreshLists = useCallback(async () => {
    const results = await Promise.allSettled([
      apiFetch("/api/processes?limit=50"),
      apiFetch("/api/allocations?limit=50"),
      apiFetch("/api/events?limit=50"),
      apiFetch("/api/jobs?since=0")
    ]);
    if (results[0].status === "fulfilled") setProcesses(results[0].value || []);
    if (results[1].status === "fulfilled") setAllocations(results[1].value || []);
    if (results[2].status === "fulfilled") setEvents(results[2].value || []);
    if (results[3].status === "fulfilled") setJobs(results[3].value || []);
  }, []);

  useEffect(() => {
    apiFetch("/api/topology")
      .then((data) => setTopology(data))
      .catch((err) => pushToast(`Topology load failed: ${err.message}`, "error"));

    pollState();
    refreshLists();

    const stateTimer = setInterval(pollState, 1000);
    const listTimer = setInterval(refreshLists, 5000);
    return () => {
      clearInterval(stateTimer);
      clearInterval(listTimer);
    };
  }, [pollState, refreshLists, pushToast]);

  const handleSelectEntity = useCallback((type, id) => {
    setSelected({ type, id });
  }, []);

  const handleSwitchPolicy = async (scheduling) => {
    try {
      const sch = await apiFetch("/api/policy", {
        method: "POST",
        body: JSON.stringify({ scheduling })
      });
      setLiveState((prev) => (prev ? { ...prev, scheduler: sch } : prev));
      pushToast(`Scheduler policy set to ${scheduling}`, "ok");
    } catch (err) {
      pushToast(`Policy switch failed: ${err.message}`, "error");
    }
  };

  const handleTriggerDisrupt = async (target, id, status) => {
    try {
      const res = await apiFetch("/api/disrupt", {
        method: "POST",
        body: JSON.stringify({ target, id, status })
      });
      if (res.solver) {
        setLiveState((prev) => (prev ? { ...prev, solver: res.solver } : prev));
      }
      pushToast(
        `${target.toUpperCase()} ${id} -> ${status}`,
        status === "DISRUPTED" ? "error" : "ok"
      );
      await Promise.all([pollState(), refreshLists()]);
    } catch (err) {
      pushToast(`Disrupt command failed: ${err.message}`, "error");
    }
  };

  return (
    <div className="flex flex-col h-screen w-screen bg-ctrl-root text-slate-100">
      <TopBar
        topology={topology}
        liveState={liveState}
        apiError={apiError}
        onSwitchPolicy={handleSwitchPolicy}
        onSync={() => {
          refreshLists();
          pushToast("Operational tables synchronized", "info");
        }}
      />

      <main className="flex-1 grid grid-cols-[270px_1fr_310px] min-h-0 bg-ctrl-border gap-px">
        <TelemetryRail topology={topology} liveState={liveState} />

        <section className="flex flex-col min-h-0 min-w-0 bg-ctrl-root">
          <LogisticsMap
            topology={topology}
            liveState={liveState}
            selected={selected}
            onSelectEntity={handleSelectEntity}
            searchFilter={searchFilter}
            onSearchChange={setSearchFilter}
          />
          <BottomConsole
            events={events}
            processes={processes}
            allocations={allocations}
            jobs={jobs}
          />
        </section>

        <InspectorRail
          topology={topology}
          liveState={liveState}
          selected={selected}
          onSelectEntity={handleSelectEntity}
          onTriggerDisrupt={handleTriggerDisrupt}
          searchFilter={searchFilter}
        />
      </main>

      {/* TOAST RACK */}
      <div className="fixed bottom-4 right-4 z-[2000] flex flex-col gap-1.5 max-w-[360px]">
        {toasts.map((t) => {
          const borderCls =
            t.kind === "error"
              ? "border-l-red-500"
              : t.kind === "ok"
              ? "border-l-emerald-500"
              : "border-l-sky-400";
          return (
            <div
              key={t.id}
              className={`bg-ctrl-elevated border border-ctrl-strong border-l-[3px] ${borderCls} px-3 py-2 font-mono text-[11px] text-slate-100 shadow-2xl`}
            >
              {t.msg}
            </div>
          );
        })}
      </div>
    </div>
  );
}
