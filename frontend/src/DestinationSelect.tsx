import { useEffect, useState } from "react";
import { api } from "./api";
import type { Country, Destination, Region, Requirements } from "./api";

export default function DestinationSelect({
  value,
  onRegionChange,
  onCity,
  disabled,
}: {
  value: Requirements;
  onRegionChange: (country: string, state: string) => void;
  onCity: (city: Destination) => void;
  disabled: boolean;
}) {
  const [countries, setCountries] = useState<Country[]>([]);
  const [states, setStates] = useState<Region[]>([]);
  const [cities, setCities] = useState<Destination[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  useEffect(() => {
    let alive = true;
    api<Country[]>("/geography/countries")
      .then((data) => {
        if (alive) setCountries(data);
      })
      .catch(() => {
        if (alive) setError("国家目录加载失败，请刷新重试");
      });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    let alive = true;
    setStates([]);
    setError("");
    api<Region[]>(
      `/geography/states?country_code=${encodeURIComponent(value.country_code)}`,
    )
      .then((data) => {
        if (alive) setStates(data);
      })
      .catch(() => {
        if (alive) setError("行政区目录加载失败，请重试");
      });
    return () => {
      alive = false;
    };
  }, [value.country_code]);
  useEffect(() => {
    let alive = true;
    if (!value.state_id) {
      setCities([]);
      return;
    }
    setLoading(true);
    const timer = setTimeout(() => {
      api<{ items: Destination[]; total: number }>(
        `/geography/cities?state_id=${encodeURIComponent(value.state_id)}&q=${encodeURIComponent(query)}`,
      )
        .then(async (data) => {
          if (!alive) return;
          let items = data.items;
          if (
            value.city_id !== null &&
            !items.some((c) => c.id === value.city_id)
          ) {
            const chosen = await api<Destination>(
              `/geography/cities/${value.city_id}`,
            );
            if (chosen.state_id === value.state_id) items = [chosen, ...items];
          }
          if (alive) {
            setCities(items);
            setTotal(data.total);
          }
        })
        .catch(() => {
          if (alive) setError("城市目录加载失败，请重试");
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, 180);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [value.state_id, value.city_id, query]);
  return (
    <fieldset className="destination-fields">
      <legend>目的地</legend>
      <div className="field">
        <label htmlFor="destination-country">国家 / 地区</label>
        <select
          id="destination-country"
          required
          disabled={disabled || !countries.length}
          value={value.country_code}
          onChange={(e) => {
            setQuery("");
            setCities([]);
            onRegionChange(e.target.value, "");
          }}
        >
          {countries.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </select>
      </div>
      <div className="field-grid">
        <div className="field">
          <label htmlFor="destination-state">省份 / 州 / 行政区</label>
          <select
            id="destination-state"
            required
            disabled={disabled || !states.length}
            value={value.state_id}
            onChange={(e) => {
              setQuery("");
              setCities([]);
              onRegionChange(value.country_code, e.target.value);
            }}
          >
            <option value="">请选择</option>
            {states.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="destination-city">城市</label>
          <select
            id="destination-city"
            required
            disabled={disabled || loading || !value.state_id}
            value={value.city_id ?? ""}
            onChange={(e) => {
              const city = cities.find((c) => c.id === Number(e.target.value));
              if (city) onCity(city);
            }}
          >
            <option value="">{loading ? "加载中…" : "请选择"}</option>
            {cities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <input
        className="city-search"
        aria-label="检索当前行政区城市"
        value={query}
        disabled={disabled || !value.state_id}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="找不到城市？输入中文或英文检索"
      />
      <p className="destination-help">
        {error ||
          (!states.length && countries.length
            ? "该地区暂无可选城市目录，请选择其他地区。"
            : `${total > 500 ? "已显示前 500 项，可检索更多。" : ""}时间按目的地当地时区安排。`)}
      </p>
    </fieldset>
  );
}
