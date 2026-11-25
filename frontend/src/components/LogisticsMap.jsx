import React, { useEffect, useRef } from "react";
import L from "leaflet";
import { feature } from "topojson-client";
import worldAtlas from "world-atlas/countries-110m.json";
import admin1Fallback from "../data/admin1.json";
import { Crosshair, Search } from "lucide-react";

const ROLE_COLORS = {
  SUPPLIER: "#38bdf8",
  HUB: "#a78bfa",
  ASSEMBLY: "#34d399"
};

const MODE_STYLE = {
  air: { color: "#38bdf8", dashArray: "6, 6", weight: 2 },
  sea: { color: "#2dd4bf", dashArray: null, weight: 2.2 },
  rail: { color: "#fbbf24", dashArray: "2, 5", weight: 2 },
  road: { color: "#94a3b8", dashArray: null, weight: 1.6 }
};

// Global Natural Earth Admin-1 (all countries' states/provinces)
const GLOBAL_ADMIN1_URL =
  "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_1_states_provinces.geojson";

function normalizeRing(ring) {
  if (!Array.isArray(ring) || ring.length === 0) return ring;

  let crossesDateline = false;
  let posCount = 0;
  let negCount = 0;

  for (let i = 0; i < ring.length; i++) {
    const lon = ring[i]?.[0];
    if (typeof lon === "number") {
      if (lon >= 0) posCount++;
      else negCount++;
      if (i > 0 && Math.abs(lon - ring[i - 1][0]) > 170) {
        crossesDateline = true;
      }
    }
  }

  if (!crossesDateline) return ring;

  const shiftToEast = posCount >= negCount;
  return ring.map(([lon, lat]) => {
    if (shiftToEast && lon < 0) return [lon + 360, lat];
    if (!shiftToEast && lon > 0) return [lon - 360, lat];
    return [lon, lat];
  });
}

function normalizeGeometry(geometry) {
  if (!geometry) return null;
  if (geometry.type === "Polygon") {
    return {
      type: "Polygon",
      coordinates: (geometry.coordinates || []).map(normalizeRing)
    };
  }
  if (geometry.type === "MultiPolygon") {
    return {
      type: "MultiPolygon",
      coordinates: (geometry.coordinates || []).map((poly) => poly.map(normalizeRing))
    };
  }
  return geometry;
}

function getCentroid(geometry) {
  let sumLon = 0;
  let sumLat = 0;
  let count = 0;

  const addRing = (ring) => {
    for (const pt of ring) {
      if (Array.isArray(pt) && typeof pt[0] === "number" && typeof pt[1] === "number") {
        let lon = pt[0];
        if (lon > 180) lon -= 360;
        if (lon < -180) lon += 360;
        sumLon += lon;
        sumLat += pt[1];
        count++;
      }
    }
  };

  if (geometry.type === "Polygon") {
    if (geometry.coordinates?.[0]) addRing(geometry.coordinates[0]);
  } else if (geometry.type === "MultiPolygon") {
    let bestRing = null;
    let bestLen = 0;
    for (const poly of geometry.coordinates || []) {
      const outer = poly?.[0];
      if (outer && outer.length > bestLen) {
        bestLen = outer.length;
        bestRing = outer;
      }
    }
    if (bestRing) addRing(bestRing);
  }

  if (!count) return null;
  return [sumLat / count, sumLon / count];
}

function buildLabelIndex(features, nameKeys) {
  return (features || [])
    .map((f) => {
      const props = f.properties || {};
      let name = null;
      for (const k of nameKeys) {
        if (props[k]) {
          name = String(props[k]);
          break;
        }
      }
      if (!name) return null;
      const pt = getCentroid(f.geometry);
      if (!pt) return null;
      const vertexCount =
        f.geometry.type === "Polygon"
          ? f.geometry.coordinates?.[0]?.length || 0
          : Math.max(...(f.geometry.coordinates || []).map((p) => p?.[0]?.length || 0));
      return { name, lat: pt[0], lon: pt[1], vertexCount };
    })
    .filter(Boolean);
}

function processAdmin1(rawGeoJson) {
  const cleanFeatures = (rawGeoJson?.features || []).map((f) => ({
    ...f,
    geometry: normalizeGeometry(f.geometry)
  }));
  return {
    geoJson: { type: "FeatureCollection", features: cleanFeatures },
    labels: buildLabelIndex(cleanFeatures, ["name", "NAME", "name_en", "gn_name"])
  };
}

const RAW_COUNTRIES = feature(worldAtlas, worldAtlas.objects.countries);

const CLEAN_COUNTRY_FEATURES = (RAW_COUNTRIES.features || []).map((f) => ({
  ...f,
  geometry: normalizeGeometry(f.geometry)
}));

const CLEAN_WORLD_GEOJSON = {
  type: "FeatureCollection",
  features: CLEAN_COUNTRY_FEATURES
};

const COUNTRY_LABELS = buildLabelIndex(CLEAN_COUNTRY_FEATURES, ["name", "NAME"]);
const FALLBACK_ADMIN1 = processAdmin1(admin1Fallback);

function toCoord(val) {
  if (val === null || val === undefined || val === "") return null;
  const num = Number(val);
  return Number.isFinite(num) ? num : null;
}

function getPrimaryMode(routeTopo, routeLive) {
  if (routeLive?.flows) {
    for (const mode of Object.keys(routeLive.flows)) {
      const resMap = routeLive.flows[mode];
      if (resMap && Object.values(resMap).some((v) => Number(v) > 0)) {
        return mode;
      }
    }
  }
  const modes = Object.keys(routeTopo.modes || {});
  return modes[0] || "road";
}

function createAdmin1Layer(geoJson, renderer) {
  return L.geoJSON(geoJson, {
    renderer,
    interactive: false,
    style: {
      fill: false,
      color: "#243244",
      weight: 0.55,
      dashArray: "2, 3",
      opacity: 0.75
    }
  });
}

function addBaseMapLayers(map, renderer) {
  const graticuleStyle = {
    color: "#1e293b",
    weight: 0.8,
    opacity: 0.35,
    interactive: false,
    renderer
  };

  for (let lat = -60; lat <= 75; lat += 15) {
    L.polyline(
      [
        [lat, -180],
        [lat, 180]
      ],
      graticuleStyle
    ).addTo(map);
  }

  for (let lon = -180; lon <= 180; lon += 30) {
    L.polyline(
      [
        [-80, lon],
        [80, lon]
      ],
      graticuleStyle
    ).addTo(map);
  }

  // Country fill + outer borders (all countries globally)
  L.geoJSON(CLEAN_WORLD_GEOJSON, {
    renderer,
    interactive: false,
    style: {
      fillColor: "#131b26",
      fillOpacity: 0.92,
      color: "#334155",
      weight: 0.9,
      opacity: 0.9
    }
  }).addTo(map);

  // Start with fallback admin1 layer; upgraded once global admin1 loads
  const admin1Layer = createAdmin1Layer(FALLBACK_ADMIN1.geoJson, renderer);
  return { admin1Layer };
}

export default function LogisticsMap({
  topology,
  liveState,
  selected,
  onSelectEntity,
  searchFilter,
  onSearchChange
}) {
  const containerRef = useRef(null);
  const labelCanvasRef = useRef(null);
  const mapRef = useRef(null);
  const rendererRef = useRef(null);
  const admin1LayerRef = useRef(null);
  const admin1LabelsRef = useRef(FALLBACK_ADMIN1.labels);
  const nodeLayersRef = useRef(new Map());
  const routeLayersRef = useRef(new Map());
  const fittedOnceRef = useRef(false);

  const fitBounds = () => {
    if (!mapRef.current || !topology?.nodes?.length) return;
    const coords = topology.nodes
      .map((n) => [toCoord(n.lat), toCoord(n.lon)])
      .filter(([lat, lon]) => lat !== null && lon !== null);
    if (coords.length > 0) {
      mapRef.current.invalidateSize();
      mapRef.current.fitBounds(coords, { padding: [45, 45], maxZoom: 6 });
    }
  };

  const syncZoomLayersAndLabels = () => {
    const map = mapRef.current;
    const canvas = labelCanvasRef.current;
    const container = containerRef.current;
    if (!map || !canvas || !container) return;

    const zoom = map.getZoom();

    // Toggle state/province boundary lines at zoom >= 4
    if (admin1LayerRef.current) {
      const hasLayer = map.hasLayer(admin1LayerRef.current);
      if (zoom >= 4 && !hasLayer) {
        admin1LayerRef.current.addTo(map);
        admin1LayerRef.current.bringToBack();
      } else if (zoom < 4 && hasLayer) {
        map.removeLayer(admin1LayerRef.current);
      }
    }

    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.floor(rect.width));
    const h = Math.max(1, Math.floor(rect.height));

    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const placedBoxes = [];
    const tryPlaceLabel = (text, lat, lon, font, fillStyle, padX = 6, padY = 3) => {
      const pt = map.latLngToContainerPoint([lat, lon]);
      if (pt.x < -80 || pt.x > w + 80 || pt.y < -25 || pt.y > h + 25) return;

      ctx.font = font;
      const textWidth = ctx.measureText(text).width;
      const box = {
        left: pt.x - textWidth / 2 - padX,
        right: pt.x + textWidth / 2 + padX,
        top: pt.y - 7 - padY,
        bottom: pt.y + 7 + padY
      };

      const overlaps = placedBoxes.some(
        (b) =>
          !(box.right < b.left || box.left > b.right || box.bottom < b.top || box.top > b.bottom)
      );
      if (overlaps) return;

      placedBoxes.push(box);

      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.strokeStyle = "rgba(9, 12, 16, 0.88)";
      ctx.lineWidth = 3;
      ctx.strokeText(text, pt.x, pt.y);

      ctx.fillStyle = fillStyle;
      ctx.fillText(text, pt.x, pt.y);
    };

    // 1. Country labels (all countries)
    const minCountryVertices = zoom <= 2 ? 40 : zoom <= 4 ? 15 : 4;
    const countryFontSize = zoom <= 2 ? 9.5 : zoom <= 4 ? 11 : 12;
    const countryFont = `600 ${countryFontSize}px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`;

    for (const c of COUNTRY_LABELS) {
      if (c.vertexCount < minCountryVertices) continue;
      tryPlaceLabel(
        c.name.toUpperCase(),
        c.lat,
        c.lon,
        countryFont,
        "rgba(203, 213, 225, 0.78)",
        8,
        4
      );
    }

    // 2. State / Province labels across all countries (appear when zooming in: zoom >= 4)
    if (zoom >= 4) {
      const minAdminVertices = zoom === 4 ? 25 : zoom === 5 ? 12 : 4;
      const adminFontSize = zoom <= 5 ? 9 : 10;
      const adminFont = `400 ${adminFontSize}px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`;

      for (const st of admin1LabelsRef.current) {
        if (st.vertexCount < minAdminVertices) continue;
        tryPlaceLabel(
          st.name,
          st.lat,
          st.lon,
          adminFont,
          "rgba(125, 211, 252, 0.62)",
          5,
          2
        );
      }
    }
  };

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    let cancelled = false;
    const renderer = L.canvas({ padding: 0.5 });
    rendererRef.current = renderer;

    const map = L.map(containerRef.current, {
      center: [25, 45],
      zoom: 2,
      minZoom: 1,
      maxZoom: 14,
      preferCanvas: true,
      zoomControl: true,
      attributionControl: false
    });

    const { admin1Layer } = addBaseMapLayers(map, renderer);
    admin1LayerRef.current = admin1Layer;
    mapRef.current = map;

    // Fetch full worldwide Admin-1 states/provinces in background
    fetch(GLOBAL_ADMIN1_URL)
      .then((res) => (res.ok ? res.json() : null))
      .then((raw) => {
        if (cancelled || !raw || !mapRef.current) return;
        const processed = processAdmin1(raw);
        admin1LabelsRef.current = processed.labels;

        if (admin1LayerRef.current && mapRef.current.hasLayer(admin1LayerRef.current)) {
          mapRef.current.removeLayer(admin1LayerRef.current);
        }
        admin1LayerRef.current = createAdmin1Layer(processed.geoJson, renderer);
        syncZoomLayersAndLabels();
      })
      .catch(() => {
        // Keep fallback admin1.geojson silently if offline
      });

    const handleRedraw = () => syncZoomLayersAndLabels();
    map.on("moveend zoomend viewreset resize", handleRedraw);

    const resizeObserver = new ResizeObserver(() => {
      if (mapRef.current) {
        mapRef.current.invalidateSize();
        syncZoomLayersAndLabels();
      }
    });
    resizeObserver.observe(containerRef.current);

    const t = setTimeout(() => {
      map.invalidateSize();
      syncZoomLayersAndLabels();
    }, 100);

    return () => {
      cancelled = true;
      clearTimeout(t);
      map.off("moveend zoomend viewreset resize", handleRedraw);
      resizeObserver.disconnect();
      map.remove();
      mapRef.current = null;
      admin1LayerRef.current = null;
      nodeLayersRef.current.clear();
      routeLayersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const renderer = rendererRef.current;
    if (!map || !renderer || !topology) return;

    map.invalidateSize();

    const nodeCoords = new Map();
    for (const n of topology.nodes || []) {
      const lat = toCoord(n.lat);
      const lon = toCoord(n.lon);
      if (lat !== null && lon !== null) {
        nodeCoords.set(n.id, [lat, lon]);
      }
    }

    const liveNodeMap = new Map((liveState?.nodes || []).map((n) => [n.id, n]));
    const liveRouteMap = new Map((liveState?.routes || []).map((r) => [r.id, r]));

    for (const rt of topology.routes || []) {
      const fromPt = nodeCoords.get(rt.from);
      const toPt = nodeCoords.get(rt.to);
      if (!fromPt || !toPt) continue;

      const lr = liveRouteMap.get(rt.id);
      const status = lr?.status || rt.status || "ACTIVE";
      const isDisrupted = status === "DISRUPTED";
      const mode = getPrimaryMode(rt, lr);
      const mCfg = MODE_STYLE[mode] || MODE_STYLE.road;
      const isSelected = selected?.type === "route" && selected?.id === rt.id;

      const style = isDisrupted
        ? { color: "#ef4444", weight: isSelected ? 4.5 : 3, dashArray: "4, 6", opacity: 0.95 }
        : { color: mCfg.color, weight: isSelected ? 4 : mCfg.weight, dashArray: mCfg.dashArray, opacity: 0.85 };

      let poly = routeLayersRef.current.get(rt.id);
      if (!poly) {
        poly = L.polyline([fromPt, toPt], { ...style, renderer });
        poly.on("click", () => onSelectEntity("route", rt.id));
        poly.addTo(map);
        routeLayersRef.current.set(rt.id, poly);
      } else {
        poly.setStyle(style);
      }
      poly.bringToFront();
      poly.bindTooltip(`${rt.id} (${rt.from} → ${rt.to}) [${status}]`, { sticky: true });
    }

    const dense = (topology.nodes || []).length > 120;
    for (const nd of topology.nodes || []) {
      const pt = nodeCoords.get(nd.id);
      if (!pt) continue;
      const ln = liveNodeMap.get(nd.id);
      const isDisrupted = Boolean(ln?.disrupted);
      const status = ln?.status || "ONLINE";
      const isSelected = selected?.type === "node" && selected?.id === nd.id;

      let fillColor = ROLE_COLORS[nd.role] || "#94a3b8";
      if (isDisrupted || status === "OFFLINE") fillColor = "#ef4444";
      else if (status === "DEGRADED") fillColor = "#f59e0b";

      const radius = isSelected ? (dense ? 7 : 10) : dense ? 4.5 : 7;
      const style = {
        radius,
        fillColor,
        color: isSelected ? "#ffffff" : "#090c10",
        weight: isSelected ? 2 : 1.5,
        fillOpacity: 0.95,
        renderer
      };

      let marker = nodeLayersRef.current.get(nd.id);
      if (!marker) {
        marker = L.circleMarker(pt, style);
        marker.on("click", () => onSelectEntity("node", nd.id));
        marker.addTo(map);
        nodeLayersRef.current.set(nd.id, marker);
      } else {
        marker.setStyle(style);
        marker.setRadius(radius);
      }
      marker.bringToFront();
      marker.bindTooltip(`${nd.id} · ${nd.name} [${nd.role} / ${status}]`);
    }

    if (!fittedOnceRef.current && nodeCoords.size > 0) {
      fitBounds();
      fittedOnceRef.current = true;
    }

    syncZoomLayersAndLabels();
  }, [topology, liveState, selected]);

  useEffect(() => {
    if (selected?.type === "node" && mapRef.current && topology?.nodes) {
      const nd = topology.nodes.find((x) => x.id === selected.id);
      const lat = toCoord(nd?.lat);
      const lon = toCoord(nd?.lon);
      if (lat !== null && lon !== null) {
        mapRef.current.panTo([lat, lon]);
      }
    }
  }, [selected, topology]);

  const hasPlottableNodes = Boolean(
    topology?.nodes?.some((n) => toCoord(n.lat) !== null && toCoord(n.lon) !== null)
  );

  return (
    <div className="relative flex-1 min-h-[250px] offline-grid-bg">
      <div ref={containerRef} className="w-full h-full" />
      <canvas
        ref={labelCanvasRef}
        className="pointer-events-none absolute inset-0 z-[450] w-full h-full"
      />

      {!hasPlottableNodes && (
        <div className="pointer-events-none absolute inset-0 z-[500] flex items-center justify-center">
          <div className="bg-ctrl-panel/90 border border-ctrl-strong px-3 py-2 font-mono text-[11px] text-slate-400">
            {topology ? "TOPOLOGY LOADED — NO LAT/LON COORDINATES IN NODES" : "WAITING FOR /api/topology..."}
          </div>
        </div>
      )}

      <div className="absolute top-2.5 left-12 right-2.5 z-[600] flex flex-wrap gap-2 pointer-events-none">
        <div className="bg-ctrl-panel/90 border border-ctrl-strong px-2.5 py-1 font-mono text-[10px] text-slate-300 flex items-center gap-2.5">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-sky-400 inline-block" /> SUPPLIER
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-violet-400 inline-block" /> HUB
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block" /> ASSEMBLY
          </span>
        </div>

        <div className="bg-ctrl-panel/90 border border-ctrl-strong px-2.5 py-1 font-mono text-[10px] text-slate-300 flex items-center gap-2.5">
          <span className="flex items-center gap-1">
            <span className="w-3.5 border-t-2 border-dashed border-sky-400 inline-block" /> AIR
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3.5 h-0.5 bg-teal-400 inline-block" /> SEA
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3.5 border-t-2 border-dotted border-amber-400 inline-block" /> RAIL
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3.5 h-0.5 bg-slate-400 inline-block" /> ROAD
          </span>
          <span className="flex items-center gap-1 text-red-400">
            <span className="w-3.5 h-[3px] bg-red-500 inline-block" /> DISRUPTED
          </span>
        </div>
      </div>

      <div className="absolute bottom-3 left-3 z-[600] flex items-center gap-1.5">
        <div className="relative flex items-center">
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 pointer-events-none" />
          <input
            type="text"
            value={searchFilter}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Filter node or route ID..."
            className="bg-ctrl-panel/95 border border-ctrl-strong focus:border-sky-400 text-slate-100 font-mono text-[11px] pl-7 pr-2.5 py-1 w-56 outline-none"
          />
        </div>
        <button
          onClick={fitBounds}
          className="flex items-center gap-1 bg-ctrl-panel/95 border border-ctrl-strong hover:bg-ctrl-hover text-slate-200 font-mono text-[10px] font-semibold px-2.5 py-1"
        >
          <Crosshair className="w-3 h-3 text-sky-400" />
          FIT NETWORK
        </button>
      </div>
    </div>
  );
}