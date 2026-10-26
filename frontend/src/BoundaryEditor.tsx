import { useState } from "react";
import { api } from "./api";
import type { Location, TravelBoundary } from "./api";

export default function BoundaryEditor({
  kind,
  value,
  city,
  center,
  onChange,
}: {
  kind: "arrival" | "departure";
  value: TravelBoundary | null;
  city: string;
  center: Location;
  onChange: (value: TravelBoundary | null) => void;
}) {
  const label = kind === "arrival" ? "抵达" : "返程";
  const [query, setQuery] = useState(value?.location.name || "");
  const [results, setResults] = useState<Location[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  async function search() {
    if (!query.trim()) {
      setNote("请填写车站、机场或地址。");
      return;
    }
    setBusy(true);
    setNote("");
    setResults([]);
    try {
      const result = await api<{ locations: Location[]; note: string }>(
        "/locations/search",
        { query: query.trim(), city, center },
      );
      setResults(result.locations);
      setNote(
        result.locations.length
          ? result.note
          : "未找到地点，请调整关键词或手动确认位置。",
      );
    } catch (error) {
      setNote(error instanceof Error ? error.message : "查询失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <fieldset className="boundary-editor">
      <legend>
        <label>
          <input
            type="checkbox"
            checked={!!value}
            onChange={(event) => {
              onChange(
                event.target.checked
                  ? {
                      location: { ...center, name: "" },
                      time: kind === "arrival" ? "12:00" : "17:00",
                      buffer_minutes: kind === "arrival" ? 30 : 60,
                    }
                  : null,
              );
              setNote("");
              setResults([]);
            }}
          />
          填写{label}地点与时间
        </label>
      </legend>
      {value && (
        <>
          <p className="field-hint">
            {kind === "arrival"
              ? "第 1 天，预留出站后再开始接驳。"
              : "最后一天，会预留候车或值机时间。"}
            使用目的地当地时间。
          </p>
          <label htmlFor={`${kind}-search`}>{label}地点搜索</label>
          <div className="boundary-search">
            <input
              id={`${kind}-search`}
              value={query}
              maxLength={100}
              placeholder="车站、机场或地址"
              onChange={(event) => {
                setQuery(event.target.value);
                setResults([]);
              }}
            />
            <button
              type="button"
              className="outline-button"
              disabled={busy}
              onClick={() => void search()}
            >
              {busy ? "查询中" : "搜索"}
            </button>
          </div>
          {note && (
            <p className="field-hint" role="status">
              {note}
            </p>
          )}
          <div className="boundary-results">
            {results.map((location, index) => (
              <button
                type="button"
                key={index}
                onClick={() => {
                  onChange({ ...value, location });
                  setQuery(location.name);
                  setResults([]);
                  setNote("已确认地点位置。");
                }}
              >
                {location.name}
                <small>
                  {location.longitude.toFixed(5)},{" "}
                  {location.latitude.toFixed(5)}
                </small>
              </button>
            ))}
          </div>
          <p className="boundary-selected">
            {value.location.name
              ? `已选：${value.location.name}`
              : "尚未确认地点，请选择结果或手动填写。"}
          </p>
          <div className="field-grid">
            <label>
              {label}时间
              <input
                type="time"
                aria-label={`${label}时间`}
                required
                value={value.time.slice(0, 5)}
                onChange={(event) =>
                  onChange({ ...value, time: event.target.value })
                }
              />
            </label>
            <label>
              {kind === "arrival" ? "出站预留分钟" : "提前到站分钟"}
              <input
                type="number"
                min={0}
                max={240}
                required
                aria-label={`${label}预留分钟`}
                value={value.buffer_minutes}
                onChange={(event) =>
                  onChange({
                    ...value,
                    buffer_minutes: Number(event.target.value),
                  })
                }
              />
            </label>
          </div>
          <details className="coordinates">
            <summary>手动确认名称与位置坐标</summary>
            <label>
              地点名称
              <input
                aria-label={`${label}地点名称`}
                maxLength={100}
                value={value.location.name}
                onChange={(event) =>
                  onChange({
                    ...value,
                    location: { ...value.location, name: event.target.value },
                  })
                }
              />
            </label>
            <div className="field-grid">
              <label>
                经度
                <input
                  aria-label={`${label}地点经度`}
                  type="number"
                  step="any"
                  min={-180}
                  max={180}
                  required
                  value={value.location.longitude}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      location: {
                        ...value.location,
                        longitude: Number(event.target.value),
                      },
                    })
                  }
                />
              </label>
              <label>
                纬度
                <input
                  aria-label={`${label}地点纬度`}
                  type="number"
                  step="any"
                  min={-90}
                  max={90}
                  required
                  value={value.location.latitude}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      location: {
                        ...value.location,
                        latitude: Number(event.target.value),
                      },
                    })
                  }
                />
              </label>
            </div>
            <label>
              坐标系
              <select
                aria-label={`${label}地点坐标系`}
                value={value.location.coordinate_system}
                onChange={(event) =>
                  onChange({
                    ...value,
                    location: {
                      ...value.location,
                      coordinate_system: event.target
                        .value as Location["coordinate_system"],
                    },
                  })
                }
              >
                <option value="GCJ-02">高德 GCJ-02</option>
                <option value="WGS84">GPS WGS84</option>
              </select>
            </label>
          </details>
        </>
      )}
    </fieldset>
  );
}
