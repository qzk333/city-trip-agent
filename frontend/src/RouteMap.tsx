import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MapPin, Maximize2, X } from "lucide-react";
import type { DayPlan, Location, Place } from "./api";
import { getBrowserConfig } from "./userConfig";

interface MapInstance {
  add: (overlays: unknown[]) => void;
  setFitView: () => void;
  destroy: () => void;
}
interface AMapApi {
  Map: new (el: HTMLElement, options: Record<string, unknown>) => MapInstance;
  Marker: new (options: Record<string, unknown>) => unknown;
  Polyline: new (options: Record<string, unknown>) => unknown;
}
declare global {
  interface Window {
    AMap?: AMapApi;
    _AMapSecurityConfig?: { securityJsCode: string };
  }
}

let mapPromise: Promise<AMapApi> | null = null;
function loadMap(): Promise<AMapApi> {
  if (window.AMap) return Promise.resolve(window.AMap);
  if (mapPromise) return mapPromise;
  mapPromise = new Promise((resolve, reject) => {
    window._AMapSecurityConfig = {
      securityJsCode: getBrowserConfig().amap_security_code,
    };
    const script = document.createElement("script");
    const timeout = setTimeout(() => reject(new Error("地图加载超时")), 12000);
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(getBrowserConfig().amap_js_key)}`;
    script.onload = () => {
      clearTimeout(timeout);
      window.AMap ? resolve(window.AMap) : reject(new Error("地图不可用"));
    };
    script.onerror = () => {
      clearTimeout(timeout);
      reject(new Error("地图加载失败"));
    };
    document.head.appendChild(script);
  });
  return mapPromise;
}

export default function RouteMap({
  day,
  places,
  hotel,
  endHotel,
  startLabel = "出发住处",
  endLabel = "住宿",
  selected,
  onSelect,
  cityName,
}: {
  day?: DayPlan;
  places: Place[];
  hotel?: Location;
  endHotel?: Location;
  startLabel?: string;
  endLabel?: string;
  selected: string | null;
  onSelect: (id: string) => void;
  cityName: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [mapReady, setMapReady] = useState(false);
  const [mapFailed, setMapFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!expanded) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", close);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", close);
    };
  }, [expanded]);
  const points = (day?.activities || [])
    .map((a) => places.find((p) => p.id === a.place_id))
    .filter((p): p is Place => !!p);
  const stops: { location: Location; id?: string; label: string }[] = [
    ...(hotel ? [{ location: hotel, label: startLabel }] : []),
    ...points.map((p, index) => ({
      location: p,
      id: p.id,
      label: String(index + 1),
    })),
    ...(endHotel ? [{ location: endHotel, label: endLabel }] : []),
  ];
  useEffect(() => {
    setMapReady(false);
    setMapFailed(false);
    if (
      !getBrowserConfig().amap_js_key ||
      !container.current ||
      !stops.length ||
      stops.some((p) => p.location.coordinate_system !== "GCJ-02")
    )
      return;
    let cancelled = false;
    let map: MapInstance | undefined;
    loadMap()
      .then((AMap) => {
        if (cancelled || !container.current) return;
        const coords = stops.map(({ location: p }) => [
          p.longitude,
          p.latitude,
        ]);
        map = new AMap.Map(container.current, {
          zoom: 13,
          center: coords[0],
          mapStyle: "amap://styles/normal",
        });
        map.add(
          coords.map(
            (position, index) =>
              new AMap.Marker({
                position,
                label: { content: stops[index].label },
              }),
          ),
        );
        if (coords.length > 1)
          map.add([
            new AMap.Polyline({
              path: coords,
              strokeColor: "#265d4c",
              strokeStyle: "dashed",
            }),
          ]);
        map.setFitView();
        setMapReady(true);
      })
      .catch(() => {
        if (!cancelled) setMapFailed(true);
      });
    return () => {
      cancelled = true;
      map?.destroy();
    };
    // Rebuild only when the displayed itinerary changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day, hotel, endHotel, expanded]);

  const coords = stops.map((p) => p.location);
  const minX = Math.min(...coords.map((p) => p.longitude)) - 0.007;
  const maxX = Math.max(...coords.map((p) => p.longitude)) + 0.007;
  const minY = Math.min(...coords.map((p) => p.latitude)) - 0.006;
  const maxY = Math.max(...coords.map((p) => p.latitude)) + 0.006;
  const xy = (p: Location) => [
    35 + ((p.longitude - minX) / (maxX - minX)) * 330,
    260 - ((p.latitude - minY) / (maxY - minY)) * 215,
  ];
  const path = coords
    .map((p, i) => `${i ? "L" : "M"} ${xy(p).join(" ")}`)
    .join(" ");
  const card = (
    <section className={`map-card${expanded ? " map-expanded" : ""}`}>
      <div className="section-label">
        <MapPin size={15} />
        <span>沿途风景</span>
        <span className="tiny-tag">DAY {day?.day || 1}</span>
        <button
          type="button"
          className="map-expand-button"
          ref={expanded ? closeButton : undefined}
          aria-label={expanded ? "关闭放大地图" : "放大地图"}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? <X size={17} /> : <Maximize2 size={16} />}
          {expanded ? "收起" : "放大"}
        </button>
      </div>
      <div
        className="map-frame"
        onDoubleClick={() => !expanded && setExpanded(true)}
      >
        <div
          ref={container}
          className={`live-map ${mapReady && !mapFailed ? "visible" : ""}`}
        />
        {(!mapReady || mapFailed) && (
          <svg
            viewBox="0 0 400 300"
            role="img"
            aria-label="地点路线示意图，并非真实导航地图"
          >
            <defs>
              <pattern
                id="blocks"
                width="42"
                height="42"
                patternUnits="userSpaceOnUse"
              >
                <path
                  d="M0 0H42V42"
                  fill="none"
                  stroke="#e2e7df"
                  strokeWidth="2"
                />
              </pattern>
            </defs>
            <rect width="400" height="300" fill="#f0f2e9" />
            <rect width="400" height="300" fill="url(#blocks)" />
            <path
              d="M0 30 Q90 5 140 80 T150 220 Q100 285 0 280Z"
              fill="#d3e3db"
            />
            <path
              d="M0 60 Q90 35 115 100 T115 225"
              fill="none"
              stroke="#bfd5c9"
              strokeWidth="2"
            />
            <text x="36" y="158" className="lake-label">
              {cityName === "杭州" ? "西 湖" : cityName}
            </text>
            {coords.length > 1 && (
              <path
                d={path}
                fill="none"
                stroke="#527a65"
                strokeWidth="2.5"
                strokeDasharray="5 6"
              />
            )}
            {stops.map(({ location: p, id, label }, i) => {
              const [x, y] = xy(p);
              return (
                <g
                  key={`${p.name}-${i}`}
                  className="map-dot"
                  onClick={() => id && onSelect(id)}
                  role={id ? "button" : undefined}
                  tabIndex={id ? 0 : undefined}
                  onKeyDown={(e) => e.key === "Enter" && id && onSelect(id)}
                  aria-label={id ? `查看${p.name}` : undefined}
                >
                  <circle
                    cx={x}
                    cy={y}
                    r={id && id === selected ? 18 : 14}
                    fill={id ? "#285647" : "#fbfaf4"}
                    stroke={id ? "#fff" : "#285647"}
                    strokeWidth="3"
                  />
                  <text
                    x={x}
                    y={y + 4}
                    textAnchor="middle"
                    fill={id ? "#fff" : "#285647"}
                    fontSize="11"
                    fontWeight="700"
                  >
                    {id ? label : "宿"}
                  </text>
                </g>
              );
            })}
            {!coords.length && (
              <text
                x="222"
                y="152"
                textAnchor="middle"
                fill="#7c8a78"
                fontSize="12"
              >
                你的旅程，即将在这里展开
              </text>
            )}
          </svg>
        )}
      </div>
      <p className="map-caption">
        {mapReady && !mapFailed
          ? "地点连线示意 · 实际路线请以导航为准"
          : "路线示意 · 非真实导航地图，坐标近似"}
      </p>
    </section>
  );
  return expanded ? (
    <>
      <div className="map-placeholder" />
      {createPortal(
        <div
          className="modal-backdrop map-viewer"
          role="dialog"
          aria-modal="true"
          aria-label={`${cityName}地图放大查看`}
          onClick={() => setExpanded(false)}
        >
          <div
            className="map-viewer-content"
            onClick={(event) => event.stopPropagation()}
          >
            {card}
          </div>
        </div>,
        document.body,
      )}
    </>
  ) : (
    card
  );
}
