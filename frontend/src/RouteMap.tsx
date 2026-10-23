import { useEffect, useRef, useState } from "react";
import { MapPin } from "lucide-react";
import type { DayPlan, Location, Place } from "./api";

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
      securityJsCode: import.meta.env.VITE_AMAP_SECURITY_CODE || "",
    };
    const script = document.createElement("script");
    const timeout = setTimeout(() => reject(new Error("地图加载超时")), 12000);
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(import.meta.env.VITE_AMAP_JS_KEY)}`;
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
  selected,
  onSelect,
}: {
  day?: DayPlan;
  places: Place[];
  hotel: Location;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [mapReady, setMapReady] = useState(false);
  const [mapFailed, setMapFailed] = useState(false);
  const points = (day?.activities || [])
    .map((a) => places.find((p) => p.id === a.place_id))
    .filter((p): p is Place => !!p);
  useEffect(() => {
    if (!import.meta.env.VITE_AMAP_JS_KEY || !container.current) return;
    let cancelled = false;
    let map: MapInstance | undefined;
    loadMap()
      .then((AMap) => {
        if (cancelled || !container.current) return;
        const coords = [
          [hotel.longitude, hotel.latitude],
          ...points.map((p) => [p.longitude, p.latitude]),
        ];
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
                label: { content: index ? String(index) : "起点" },
              }),
          ),
        );
        if (points.length)
          map.add([
            new AMap.Polyline({
              path: [...coords, coords[0]],
              strokeColor: "#265d4c",
              strokeStyle: "dashed",
            }),
          ]);
        map.setFitView();
        setMapReady(true);
      })
      .catch(() => setMapFailed(true));
    return () => {
      cancelled = true;
      map?.destroy();
    };
    // Rebuild only when the displayed itinerary changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day, hotel]);

  const coords = [hotel, ...points];
  const minX = Math.min(...coords.map((p) => p.longitude)) - 0.007;
  const maxX = Math.max(...coords.map((p) => p.longitude)) + 0.007;
  const minY = Math.min(...coords.map((p) => p.latitude)) - 0.006;
  const maxY = Math.max(...coords.map((p) => p.latitude)) + 0.006;
  const xy = (p: Location) => [
    35 + ((p.longitude - minX) / (maxX - minX)) * 330,
    260 - ((p.latitude - minY) / (maxY - minY)) * 215,
  ];
  const path = [...coords, hotel]
    .map((p, i) => `${i ? "L" : "M"} ${xy(p).join(" ")}`)
    .join(" ");
  return (
    <section className="map-card">
      <div className="section-label">
        <MapPin size={15} />
        <span>沿途风景</span>
        <span className="tiny-tag">DAY {day?.day || 1}</span>
      </div>
      <div className="map-frame">
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
              西 湖
            </text>
            {points.length > 0 && (
              <path
                d={path}
                fill="none"
                stroke="#527a65"
                strokeWidth="2.5"
                strokeDasharray="5 6"
              />
            )}
            {coords.map((p, i) => {
              const [x, y] = xy(p);
              return (
                <g
                  key={`${p.name}-${i}`}
                  className="map-dot"
                  onClick={() => i > 0 && onSelect(points[i - 1].id)}
                  role={i ? "button" : undefined}
                  tabIndex={i ? 0 : undefined}
                  onKeyDown={(e) =>
                    e.key === "Enter" && i > 0 && onSelect(points[i - 1].id)
                  }
                  aria-label={i ? `查看${p.name}` : undefined}
                >
                  <circle
                    cx={x}
                    cy={y}
                    r={i > 0 && points[i - 1].id === selected ? 18 : 14}
                    fill={i === 0 ? "#fbfaf4" : "#285647"}
                    stroke={i === 0 ? "#285647" : "#fff"}
                    strokeWidth="3"
                  />
                  <text
                    x={x}
                    y={y + 4}
                    textAnchor="middle"
                    fill={i === 0 ? "#285647" : "#fff"}
                    fontSize="11"
                    fontWeight="700"
                  >
                    {i || "宿"}
                  </text>
                </g>
              );
            })}
            {!points.length && (
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
}
