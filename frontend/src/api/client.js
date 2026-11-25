const params = new URLSearchParams(window.location.search);
export const IS_MOCK = params.get("mock") === "1";

let mockStateCache = null;
let mockEventsCache = null;

async function mockFetch(url, options = {}) {
  const cleanPath = url.split("?")[0];

  if (cleanPath === "/api/topology") {
    const res = await fetch("./mock/topology.json");
    return res.json();
  }
  if (cleanPath === "/api/state") {
    if (!mockStateCache) {
      const res = await fetch("./mock/state.json");
      mockStateCache = await res.json();
    }
    mockStateCache.time = new Date().toISOString().slice(0, 19);
    return structuredClone(mockStateCache);
  }
  if (cleanPath === "/api/processes") {
    const res = await fetch("./mock/processes.json");
    return res.json();
  }
  if (cleanPath === "/api/allocations") {
    const res = await fetch("./mock/allocations.json");
    return res.json();
  }
  if (cleanPath === "/api/events") {
    if (!mockEventsCache) {
      const res = await fetch("./mock/events.json");
      mockEventsCache = await res.json();
    }
    return structuredClone(mockEventsCache);
  }
  if (cleanPath === "/api/jobs") {
    const res = await fetch("./mock/jobs.json");
    return res.json();
  }
  if (cleanPath === "/api/policy" && options.method === "POST") {
    const body = JSON.parse(options.body || "{}");
    if (mockStateCache?.scheduler) {
      mockStateCache.scheduler.policy = body.scheduling || "PRIORITY";
    }
    return structuredClone(mockStateCache.scheduler);
  }
  if (cleanPath === "/api/disrupt" && options.method === "POST") {
    const body = JSON.parse(options.body || "{}");
    const t0 = performance.now();

    if (body.target === "route") {
      const rt = (mockStateCache.routes || []).find((x) => x.id === body.id);
      if (rt) {
        rt.status = body.status;
        if (body.status === "DISRUPTED") rt.flows = {};
      }
    } else if (body.target === "node") {
      const nd = (mockStateCache.nodes || []).find((x) => x.id === body.id);
      if (nd) {
        nd.disrupted = body.status === "DISRUPTED";
        nd.status = nd.disrupted ? "OFFLINE" : "ONLINE";
      }
    }

    const disruptedCount =
      (mockStateCache.routes || []).filter((r) => r.status === "DISRUPTED").length +
      (mockStateCache.nodes || []).filter((n) => n.disrupted).length;

    const baseShip = 118420.0;
    const rerouteDelta = disruptedCount * 14350.0;
    const solveMs = Math.max(4, Math.round(performance.now() - t0 + 11));

    mockStateCache.solver = {
      status: "OPTIMAL",
      objective_cost: baseShip + rerouteDelta,
      shipping_cost: baseShip + rerouteDelta,
      shortage_cost: 0.0,
      solve_ms: solveMs,
      unmet: {},
      reason: body.status === "DISRUPTED" ? "disruption" : "manual",
      computed_at: new Date().toISOString().slice(0, 19)
    };

    if (mockEventsCache) {
      mockEventsCache.unshift({
        event_id: `EV-${Date.now().toString().slice(-4)}`,
        type: body.status === "DISRUPTED" ? "DISRUPTION" : "RESTORATION",
        severity: body.status === "DISRUPTED" ? "CRITICAL" : "INFO",
        description: `${body.target.toUpperCase()} ${body.id} set to ${body.status}; LP re-solved in ${solveMs} ms`,
        time: new Date().toISOString().slice(11, 19),
        node: body.target === "node" ? body.id : "CENTRE"
      });
    }

    return { applied: true, solver: structuredClone(mockStateCache.solver) };
  }

  throw new Error(`Unsupported mock endpoint: ${url}`);
}

export async function apiFetch(url, options = {}) {
  if (IS_MOCK) {
    return mockFetch(url, options);
  }
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  const text = await res.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { error: text || `HTTP ${res.status}` };
  }
  if (!res.ok) {
    throw new Error(payload.error || `HTTP ${res.status}`);
  }
  return payload;
}

export function fmtNum(val, decimals = 0) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "—";
  return Number(val).toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

export function fmtCurrency(val, currency = "USD") {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "—";
  return `${currency} ${fmtNum(val, 2)}`;
}

export function fmtVal(val, suffix = "") {
  if (val === null || val === undefined || val === "") return "—";
  return `${val}${suffix}`;
}
