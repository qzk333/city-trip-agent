import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowDown,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  CloudSun,
  Compass,
  Footprints,
  History,
  Leaf,
  LoaderCircle,
  LockKeyhole,
  MapPin,
  Navigation,
  Plus,
  RotateCcw,
  Route,
  ShieldCheck,
  Settings2,
  Sparkles,
  TrainFront,
  Trash2,
  Umbrella,
  UnlockKeyhole,
  X,
} from "lucide-react";
import { api, waitForRun } from "./api";
import type {
  Action,
  Activity,
  Destination,
  Health,
  Itinerary,
  Place,
  Proposal,
  Requirements,
  Run,
  RunEvent,
  Trip,
  Version,
} from "./api";
import RouteMap from "./RouteMap";
import ApiSettings from "./ApiSettings";
import DestinationSelect from "./DestinationSelect";
import {
  clearBrowserConfig,
  getBrowserConfig,
  saveBrowserConfig,
} from "./userConfig";
import type { UserAPIConfig } from "./userConfig";

const storedTripKey = "city-trip:last-trip";
function tomorrow() {
  const now = new Date();
  now.setDate(now.getDate() + 1);
  return now.toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
}
const initial: Requirements = {
  city: "杭州",
  country_code: "CN",
  country: "中国",
  state_id: "CN-ZJ",
  province: "浙江省",
  city_id: 19598,
  timezone: "Asia/Shanghai",
  catalog_id: null,
  start_date: tomorrow(),
  days: 1,
  day_start: "09:00",
  day_end: "18:00",
  hotel: {
    name: "演示起点 · 湖滨",
    longitude: 120.163,
    latitude: 30.251,
    coordinate_system: "GCJ-02",
  },
  transport: "transit",
  pace: "relaxed",
  interests: ["nature", "culture"],
  must_visit: ["westlake", "gushan_museum"],
  appointments: [],
  note: "",
};
const statusNames: Record<Itinerary["status"], string> = {
  FEASIBLE: "已通过已知约束检查",
  NEEDS_CONFIRMATION: "有信息需要出行前确认",
  CONFLICT: "发现条件冲突",
  NO_SOLUTION_WITHIN_LIMIT: "暂未找到满足条件的方案",
};
const interestNames: Record<Place["category"], string> = {
  nature: "自然风景",
  culture: "人文展览",
  coffee: "咖啡休息",
  city: "城市漫步",
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [places, setPlaces] = useState<Place[]>([]);
  const [form, setForm] = useState<Requirements>(initial);
  const [trip, setTrip] = useState<Trip | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedDay, setSelectedDay] = useState(1);
  const [selectedPlace, setSelectedPlace] = useState<string | null>(null);
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [appointmentOn, setAppointmentOn] = useState(false);
  const [delay, setDelay] = useState(40);
  const [showForm, setShowForm] = useState(true);
  const [config, setConfig] = useState<UserAPIConfig>(getBrowserConfig);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const destinationRequest = useRef(0);
  const plan = proposal?.itinerary || trip?.itinerary || null;
  const day = plan?.days.find((d) => d.day === selectedDay) || plan?.days[0];
  const activeReq = trip?.requirements || form;
  const errors = plan?.checks.filter((c) => c.severity === "error") || [];
  const canApply =
    !!proposal &&
    errors.length === 0 &&
    !["CONFLICT", "NO_SOLUTION_WITHIN_LIMIT"].includes(
      proposal.itinerary.status,
    );
  const totalActivities =
    plan?.days.reduce((n, d) => n + d.activities.length, 0) || 0;
  const transitMinutes =
    day?.legs.reduce((n, leg) => n + leg.duration_minutes, 0) || 0;
  const startMinutes =
    Number(activeReq.day_start.slice(0, 2)) * 60 +
    Number(activeReq.day_start.slice(3, 5)) +
    (plan?.day_start_offsets?.[String(day?.day || 1)] || 0);
  const displayedStart = `${String(Math.floor(startMinutes / 60)).padStart(2, "0")}:${String(startMinutes % 60).padStart(2, "0")}`;
  const activeToolProvider = plan?.tool_provider || health?.tool_provider;
  const planPlaces = trip?.places || places;
  const placeById = (id: string) => planPlaces.find((p) => p.id === id);
  const catalogReady =
    config.tool_provider === "demo" ||
    (!!form.catalog_id && places.length > 0 && places.every((p) => !p.fixture));
  const fixedExampleAvailable = places.some((p) => p.id === "gushan_museum");

  async function loadTrip(id: string) {
    const saved = await api<Trip>(`/trips/${id}`);
    const requirements = {
      ...initial,
      ...saved.requirements,
      city_id: saved.requirements.city_id ?? 19598,
    };
    setTrip({ ...saved, requirements });
    setForm(requirements);
    setPlaces(saved.places);
    setAppointmentOn(saved.requirements.appointments.length > 0);
    return saved;
  }

  function changeRegion(country: string, state: string) {
    destinationRequest.current++;
    setPlaces([]);
    setAppointmentOn(false);
    setForm((f) => ({
      ...f,
      country_code: country,
      state_id: state,
      city: "待选择",
      timezone: "",
      city_id: null,
      catalog_id: null,
      must_visit: [],
      appointments: [],
    }));
  }

  async function selectCity(city: Destination) {
    const ticket = ++destinationRequest.current;
    setCatalogLoading(true);
    setError("");
    setAppointmentOn(false);
    try {
      const [destination, catalog] = await Promise.all([
        api<Destination>(`/geography/cities/${city.id}`),
        api<Place[]>(`/places?city_id=${city.id}`),
      ]);
      if (ticket !== destinationRequest.current) return;
      setPlaces(catalog);
      setForm((f) => ({
        ...f,
        city: destination.name,
        city_id: destination.id,
        country_code: destination.country_code,
        state_id: destination.state_id,
        country: destination.country || "",
        province: destination.state || "",
        timezone: destination.timezone || "",
        catalog_id: null,
        must_visit: [],
        appointments: [],
        hotel: {
          name: `参考起点 · ${destination.name}`,
          longitude: destination.longitude,
          latitude: destination.latitude,
          coordinate_system: destination.coordinate_system,
        },
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "城市加载失败");
    } finally {
      if (ticket === destinationRequest.current) setCatalogLoading(false);
    }
  }

  async function discoverPlaces() {
    if (form.city_id === null) return;
    setCatalogLoading(true);
    setError("");
    const ticket = ++destinationRequest.current;
    try {
      const result = await api<{
        catalog_id: string;
        places: Place[];
        center: Requirements["hotel"];
      }>("/places/discover", { city_id: form.city_id });
      if (ticket !== destinationRequest.current) return;
      setPlaces(result.places);
      setAppointmentOn(false);
      setForm((f) => ({
        ...f,
        catalog_id: result.catalog_id,
        hotel: result.center,
        must_visit: [],
        appointments: [],
      }));
      setNotice("已载入当前城市地点。请填写实际住处位置，并选择想去的地方。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "地点查询失败");
    } finally {
      if (ticket === destinationRequest.current) setCatalogLoading(false);
    }
  }

  async function saveSettings(next: UserAPIConfig, clear = false) {
    const info = await api<Health>("/health", undefined, next);
    const refreshMap =
      next.amap_js_key !== config.amap_js_key ||
      next.amap_security_code !== config.amap_security_code;
    if (clear) clearBrowserConfig();
    else saveBrowserConfig(next);
    setConfig(next);
    setHealth(info);
    setSettingsOpen(false);
    setNotice(
      clear
        ? "已清除本标签页的 API 配置，切回演示模式。"
        : "API 设置已保存在当前标签页。真实地图模式请先查询城市地点。",
    );
    if (refreshMap) window.location.reload();
  }

  useEffect(() => {
    let alive = true;
    async function init() {
      try {
        const [info, catalog] = await Promise.all([
          api<Health>("/health"),
          api<Place[]>("/places"),
        ]);
        if (!alive) return;
        setHealth(info);
        setPlaces(catalog);
        const id = localStorage.getItem(storedTripKey);
        if (id) {
          try {
            const saved = await loadTrip(id);
            if (!alive) return;
            if (saved.latest_run_id) {
              const run = await api<Run>(`/runs/${saved.latest_run_id}`);
              if (!alive) return;
              setEvents(run.events);
              if (
                run.proposal &&
                !run.proposal.dismissed &&
                run.proposal.applied_version === null &&
                run.proposal.base_version === saved.current_version
              )
                setProposal(run.proposal);
              if (["queued", "running"].includes(run.status)) {
                setBusy(true);
                try {
                  const finished = await waitForRun(run.id, setEvents);
                  if (alive) setProposal(finished.proposal);
                } finally {
                  if (alive) setBusy(false);
                }
              }
              if (run.status === "failed")
                setNotice(run.error || "上次任务失败，可以重新生成");
            }
          } catch {
            if (alive) setNotice("上次旅行无法恢复，可以创建新行程。");
          }
        }
      } catch (e) {
        if (alive)
          setError(e instanceof Error ? e.message : "无法连接本地服务");
      } finally {
        if (alive) setLoading(false);
      }
    }
    void init();
    return () => {
      alive = false;
    };
  }, []);

  async function generate(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    setProposal(null);
    setEvents([]);
    try {
      const req = {
        ...form,
        appointments:
          appointmentOn && fixedExampleAvailable
            ? [
                {
                  place_id: "gushan_museum",
                  day: 1,
                  start: "14:00",
                  end: "15:30",
                },
              ]
            : [],
      };
      const created = await api<Trip>("/trips", req);
      localStorage.setItem(storedTripKey, created.id);
      setTrip(created);
      setSelectedDay(1);
      const { run_id } = await api<{ run_id: string }>(
        `/trips/${created.id}/plan-runs`,
        {},
      );
      const run = await waitForRun(run_id, setEvents);
      setProposal(run.proposal);
      if (window.innerWidth < 900) setShowForm(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function adjust(action: Action, activity?: Activity) {
    if (!trip || proposal) return;
    setBusy(true);
    setError("");
    setNotice("");
    setEvents([]);
    try {
      const { run_id } = await api<{ run_id: string }>(
        `/trips/${trip.id}/proposals`,
        {
          base_version: trip.current_version,
          action,
          day: activity?.day || selectedDay,
          activity_id: activity?.id || null,
          delay_minutes: delay,
        },
      );
      const run = await waitForRun(run_id, setEvents);
      setProposal(run.proposal);
    } catch (e) {
      setError(e instanceof Error ? e.message : "调整失败");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!trip || !proposal) return;
    setBusy(true);
    setError("");
    try {
      await api(`/trips/${trip.id}/proposals/${proposal.id}/apply`, {
        base_version: proposal.base_version,
        idempotency_key: `confirm-${proposal.id}`,
      });
      await loadTrip(trip.id);
      setProposal(null);
      setNotice("行程已保存，刷新页面也可以继续查看。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function dismiss() {
    if (!trip || !proposal) return;
    setBusy(true);
    setError("");
    try {
      await api(`/trips/${trip.id}/proposals/${proposal.id}/dismiss`, {});
      setProposal(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败");
    } finally {
      setBusy(false);
    }
  }

  async function history() {
    if (!trip) return;
    try {
      setVersions(await api<Version[]>(`/trips/${trip.id}/versions`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }

  async function restore(number: number) {
    if (!trip) return;
    setBusy(true);
    setError("");
    try {
      await api(`/trips/${trip.id}/versions/${number}/restore`, {
        base_version: trip.current_version,
        idempotency_key: `restore-${trip.current_version}-${number}`,
      });
      await loadTrip(trip.id);
      setProposal(null);
      setVersions(null);
      setNotice("已将历史行程恢复为新版本。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "恢复失败");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    destinationRequest.current++;
    localStorage.removeItem(storedTripKey);
    setTrip(null);
    setProposal(null);
    setEvents([]);
    setNotice("");
    setError("");
    setForm(initial);
    setAppointmentOn(false);
    setSelectedDay(1);
    setShowForm(true);
    setCatalogLoading(false);
    void api<Place[]>("/places")
      .then(setPlaces)
      .catch(() => setError("地点加载失败，请刷新重试"));
  }
  function toggleMust(id: string) {
    setForm((f) => ({
      ...f,
      must_visit: f.must_visit.includes(id)
        ? f.must_visit.filter((p) => p !== id)
        : [...f.must_visit, id],
    }));
  }
  const weather = plan?.weather.data.days?.find((w) => w.date === day?.date);
  const previousActivities =
    trip?.itinerary?.days.flatMap((d) => d.activities) || [];
  const newActivities = plan?.days.flatMap((d) => d.activities) || [];
  const added = newActivities.filter(
    (a) => !previousActivities.some((b) => b.id === a.id),
  );
  const removed = previousActivities.filter(
    (a) => !newActivities.some((b) => b.id === a.id),
  );
  const changed = newActivities.filter((a) =>
    previousActivities.some(
      (b) =>
        b.id === a.id &&
        (b.start !== a.start || b.end !== a.end || b.locked !== a.locked),
    ),
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="行间首页">
          <span className="brand-icon">
            <Compass size={24} />
          </span>
          <span>
            行间<span className="brand-en">CITY COMPANION</span>
          </span>
        </a>
        <div className="topbar-center">
          <span className="status-dot" />
          把想去的地方，变成走得通的旅行
        </div>
        <div className="topbar-actions">
          <span className="mode-pill">
            {config.planning_mode === "llm" ? "我的模型" : "规则规划"} ·{" "}
            {config.tool_provider === "amap" ? "我的地图" : "演示地图"}
          </span>
          <button
            className="text-button settings-trigger"
            disabled={busy}
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2 size={16} />
            API 设置
          </button>
          <button
            className="text-button"
            onClick={reset}
            disabled={busy || loading}
          >
            <Plus size={16} />
            新行程
          </button>
        </div>
      </header>

      <main className="workspace">
        <aside className={`sidebar ${showForm ? "form-visible" : ""}`}>
          <div className="sidebar-heading">
            <span className="eyebrow">LET'S GO SOMEWHERE</span>
            <h2>这次，想怎么逛？</h2>
            <p>告诉我你的安排，剩下的一起理顺。</p>
          </div>
          <form onSubmit={generate}>
            <DestinationSelect
              value={form}
              onRegionChange={changeRegion}
              onCity={(city) => void selectCity(city)}
              disabled={busy || catalogLoading}
            />
            {config.tool_provider === "amap" && (
              <div className="catalog-query">
                <p>
                  {form.country_code === "CN"
                    ? "使用自己的 Key 查询当前城市景点与休息点。"
                    : "境外可选演示目的地，当前高德标准接口仅支持中国大陆。"}
                </p>
                <button
                  type="button"
                  className="outline-button"
                  disabled={
                    busy ||
                    catalogLoading ||
                    form.city_id === null ||
                    form.country_code !== "CN"
                  }
                  onClick={() => void discoverPlaces()}
                >
                  {catalogLoading ? (
                    <LoaderCircle size={14} className="spin" />
                  ) : (
                    <MapPin size={14} />
                  )}
                  查询城市真实地点
                </button>
              </div>
            )}
            <div className="field-grid">
              <div className="field">
                <label htmlFor="start-date">出发日期</label>
                <input
                  id="start-date"
                  type="date"
                  required
                  value={form.start_date}
                  onChange={(e) =>
                    setForm({ ...form, start_date: e.target.value })
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="days">旅行天数</label>
                <select
                  id="days"
                  value={form.days}
                  onChange={(e) =>
                    setForm({ ...form, days: Number(e.target.value) })
                  }
                >
                  <option value={1}>1 天</option>
                  <option value={2}>2 天</option>
                  <option value={3}>3 天</option>
                </select>
              </div>
            </div>
            <div className="field-grid">
              <div className="field">
                <label htmlFor="day-start">每日出发</label>
                <input
                  id="day-start"
                  type="time"
                  required
                  value={form.day_start.slice(0, 5)}
                  onChange={(e) =>
                    setForm({ ...form, day_start: e.target.value })
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="day-end">返回住处</label>
                <input
                  id="day-end"
                  type="time"
                  required
                  value={form.day_end.slice(0, 5)}
                  onChange={(e) =>
                    setForm({ ...form, day_end: e.target.value })
                  }
                />
              </div>
            </div>
            <div className="field">
              <label htmlFor="hotel">住处 / 每日出发点</label>
              <input
                id="hotel"
                required
                maxLength={100}
                value={form.hotel.name}
                onChange={(e) =>
                  setForm({
                    ...form,
                    hotel: { ...form.hotel, name: e.target.value },
                  })
                }
              />
              <details className="coordinates">
                <summary>
                  调整位置坐标（{form.hotel.coordinate_system}）
                </summary>
                <label className="coordinate-system-label">
                  坐标系
                  <select
                    aria-label="住处坐标系"
                    value={form.hotel.coordinate_system}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        hotel: {
                          ...form.hotel,
                          coordinate_system: e.target
                            .value as Requirements["hotel"]["coordinate_system"],
                        },
                      })
                    }
                  >
                    <option value="GCJ-02">高德 GCJ-02</option>
                    <option value="WGS84">GPS / 地理目录 WGS84</option>
                  </select>
                </label>
                <div className="field-grid">
                  <label>
                    经度
                    <input
                      aria-label="住处经度"
                      type="number"
                      min="-180"
                      max="180"
                      step="any"
                      required
                      value={form.hotel.longitude}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          hotel: {
                            ...form.hotel,
                            longitude: Number(e.target.value),
                          },
                        })
                      }
                    />
                  </label>
                  <label>
                    纬度
                    <input
                      aria-label="住处纬度"
                      type="number"
                      min="-90"
                      max="90"
                      step="any"
                      required
                      value={form.hotel.latitude}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          hotel: {
                            ...form.hotel,
                            latitude: Number(e.target.value),
                          },
                        })
                      }
                    />
                  </label>
                </div>
              </details>
            </div>
            <fieldset>
              <legend>出行方式</legend>
              <div className="segmented">
                <button
                  type="button"
                  className={form.transport === "transit" ? "selected" : ""}
                  onClick={() => setForm({ ...form, transport: "transit" })}
                >
                  <TrainFront size={15} />
                  公共交通
                </button>
                <button
                  type="button"
                  className={form.transport === "walking" ? "selected" : ""}
                  onClick={() => setForm({ ...form, transport: "walking" })}
                >
                  <Footprints size={15} />
                  全程步行
                </button>
              </div>
            </fieldset>
            <fieldset>
              <legend>旅行节奏</legend>
              <div className="segmented">
                <button
                  type="button"
                  className={form.pace === "relaxed" ? "selected" : ""}
                  onClick={() => setForm({ ...form, pace: "relaxed" })}
                >
                  <Leaf size={15} />
                  慢慢逛
                </button>
                <button
                  type="button"
                  className={form.pace === "balanced" ? "selected" : ""}
                  onClick={() => setForm({ ...form, pace: "balanced" })}
                >
                  <Compass size={15} />
                  多探索
                </button>
              </div>
            </fieldset>
            <fieldset>
              <legend>感兴趣的体验</legend>
              <div className="interest-options">
                {Object.entries(interestNames).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={form.interests.includes(
                      key as Place["category"],
                    )}
                    className={
                      form.interests.includes(key as Place["category"])
                        ? "active"
                        : ""
                    }
                    onClick={() =>
                      setForm({
                        ...form,
                        interests: form.interests.includes(
                          key as Place["category"],
                        )
                          ? form.interests.filter((i) => i !== key)
                          : [...form.interests, key as Place["category"]],
                      })
                    }
                  >
                    {label}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend>
                一定想去{" "}
                <span className="legend-hint">
                  {form.must_visit.length} 个地点
                </span>
              </legend>
              <div className="place-options">
                {places.map((p) => (
                  <label
                    key={p.id}
                    className={form.must_visit.includes(p.id) ? "checked" : ""}
                  >
                    <input
                      type="checkbox"
                      checked={form.must_visit.includes(p.id)}
                      onChange={() => toggleMust(p.id)}
                    />
                    <span>{p.name}</span>
                    {form.must_visit.includes(p.id) && <Check size={14} />}
                  </label>
                ))}
              </div>
            </fieldset>
            {fixedExampleAvailable && (
              <label className="appointment-toggle">
                <input
                  type="checkbox"
                  checked={appointmentOn}
                  onChange={(e) => setAppointmentOn(e.target.checked)}
                />
                <span>
                  <strong>保留一项固定预约</strong>
                  <small>
                    第 1 天 · 孤山馆区 · 14:00—15:30
                    <br />
                    演示时段，并非真实预约
                  </small>
                </span>
                <LockKeyhole size={16} />
              </label>
            )}
            <div className="field">
              <label htmlFor="note">
                偏好补充{" "}
                <span className="optional">
                  {health?.planning_mode === "llm"
                    ? "模型参与理解"
                    : "模型模式下参与规划"}
                </span>
              </label>
              <textarea
                id="note"
                maxLength={1000}
                rows={2}
                value={form.note}
                placeholder="例如：更喜欢艺术展览，少安排购物。"
                onChange={(e) => setForm({ ...form, note: e.target.value })}
              />
            </div>
            <button
              className="primary generate-button"
              type="submit"
              disabled={
                busy ||
                loading ||
                catalogLoading ||
                !health ||
                form.city_id === null ||
                !form.timezone ||
                !catalogReady
              }
            >
              {busy ? (
                <LoaderCircle size={17} className="spin" />
              ) : (
                <Sparkles size={17} />
              )}
              {busy
                ? "正在整理你的旅程…"
                : trip
                  ? "按这些条件生成新行程"
                  : "生成我的行程"}
              {!busy && <ArrowRight size={17} />}
            </button>
            <p className="form-footnote">
              支持 1—3 天城市旅行。
              {!catalogReady
                ? "请先查询真实地点，或在 API 设置中切回演示模式。"
                : "演示占位与真实查询会分别标注。"}
            </p>
          </form>
        </aside>

        <div className="main-area">
          <button
            className="mobile-form-button"
            onClick={() => setShowForm(!showForm)}
          >
            {showForm ? "收起条件" : "编辑旅行条件"}
            <ChevronRight size={16} />
          </button>
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                A LITTLE PLANNING, A LOT OF WANDERING
              </div>
              <h1>
                留点时间，<span>给沿途。</span>
              </h1>
              <p>行程有安排，也有临时改变的余地。</p>
            </div>
            <div className="heading-stamp">
              <span>CITY COMPANION</span>
              <strong>{activeReq.city}</strong>
              <small>慢一点，发现多一点</small>
            </div>
          </div>
          <div className="data-notice">
            <ShieldCheck size={17} />
            <p>
              <strong>
                {activeToolProvider === "amap"
                  ? "已选择真实交通查询。"
                  : "当前使用固定演示数据。"}
              </strong>{" "}
              {planPlaces.every((p) => p.fixture)
                ? "地点为演示夹具或占位活动，路线"
                : "地点来自地图查询，营业与停留时间待核实；路线"}
              {activeToolProvider === "amap" ? "为查询时估计" : "为距离估算"}
              ；天气未知时会明确保留，不作为真实出行保证。
            </p>
            <span className="notice-label">透明依据</span>
          </div>
          {error && (
            <div className="message error" role="alert">
              <span>{error}</span>
              <button aria-label="关闭错误" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="message success" role="status">
              <CheckCircle2 size={17} />
              <span>{notice}</span>
              <button aria-label="关闭提示" onClick={() => setNotice("")}>
                <X size={16} />
              </button>
            </div>
          )}
          <section className="trip-heading">
            <div>
              <span className="location-kicker">
                <MapPin size={13} />
                {activeReq.city} · 城市轻旅行
              </span>
              <h2>
                {plan
                  ? `${activeReq.days} 天，把日子过得松弛一点`
                  : `你的${activeReq.city}小旅行`}
              </h2>
              <p>
                {plan
                  ? `${activeReq.start_date.replaceAll("-", ".")} 出发 · ${totalActivities} 个活动 · ${activeReq.transport === "transit" ? "公共交通" : "步行"}优先`
                  : "从想去的地方开始，我们一起把路线理顺。"}
              </p>
            </div>
            <button
              className="outline-button"
              disabled={!trip?.current_version || busy}
              onClick={() => void history()}
            >
              <History size={15} />
              版本记录
              {trip?.current_version ? (
                <span>v{trip.current_version}</span>
              ) : null}
            </button>
          </section>

          {proposal && (
            <section
              className={`proposal-bar ${canApply ? "" : "has-conflict"}`}
              aria-label="行程预览"
            >
              <div>
                <span className="eyebrow">
                  {trip?.current_version
                    ? "CHANGE PREVIEW"
                    : "YOUR FIRST DRAFT"}
                </span>
                <h3>{proposal.label} · 尚未保存</h3>
                <p>
                  {trip?.current_version
                    ? `新增 ${added.length} 项，移除 ${removed.length} 项，调整 ${changed.length} 项。`
                    : "先看看路线和待确认事项，满意后再保存。"}
                </p>
                {trip?.current_version ? (
                  <div className="change-list">
                    {removed.map((a) => (
                      <span key={a.id}>− {placeById(a.place_id)?.name}</span>
                    ))}
                    {added.map((a) => (
                      <span key={a.id}>＋ {placeById(a.place_id)?.name}</span>
                    ))}
                    {changed.map((a) => (
                      <span key={a.id}>
                        ↻ {placeById(a.place_id)?.name} {a.start}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="proposal-actions">
                {trip?.current_version ? (
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => void dismiss()}
                  >
                    取消调整
                  </button>
                ) : null}
                <button
                  className="primary"
                  disabled={busy || !canApply}
                  onClick={() => void confirm()}
                >
                  <Check size={16} />
                  {activeToolProvider === "demo"
                    ? "保存演示行程"
                    : "了解估算并保存"}
                </button>
              </div>
            </section>
          )}

          {busy && (
            <div className="progress-card" role="status">
              <LoaderCircle size={18} className="spin" />
              <div>
                <strong>{events.at(-1)?.message || "正在开始规划…"}</strong>
                <p>只提交检查完成的结果，你已保存的行程会保留。</p>
              </div>
            </div>
          )}

          <div className="content-grid">
            <section className="itinerary-column">
              <div className="day-tabs">
                {Array.from({ length: plan?.days.length || 1 }, (_, i) => (
                  <button
                    key={i}
                    className={selectedDay === i + 1 ? "active" : ""}
                    onClick={() => setSelectedDay(i + 1)}
                  >
                    DAY {String(i + 1).padStart(2, "0")}
                    <span>
                      {i === 0
                        ? `初见${activeReq.city}`
                        : i === 1
                          ? "再走走看"
                          : "不急着告别"}
                    </span>
                  </button>
                ))}
                <span className="tab-line" />
              </div>
              {plan && day ? (
                <>
                  <div className="day-summary">
                    <span>
                      <Clock3 size={14} />
                      {displayedStart} 出发
                    </span>
                    <span>
                      <Route size={14} />
                      路途约 {transitMinutes} 分钟
                    </span>
                    <span className="summary-pace">
                      <Leaf size={14} />
                      {activeReq.pace === "relaxed" ? "慢慢逛" : "多探索"}
                    </span>
                  </div>
                  {!day.activities.length && (
                    <div className="empty-small">
                      当前条件下没有排入活动。请查看下方检查结果，或增加可用时间。
                    </div>
                  )}
                  <div className="timeline">
                    <div className="hotel-marker">
                      <span className="small-dot" />
                      <div>
                        <strong>{activeReq.hotel.name}</strong>
                        <p>从住处出发</p>
                      </div>
                    </div>
                    {day.activities.map((activity, index) => {
                      const place = placeById(activity.place_id);
                      const leg = day.legs[index];
                      return (
                        <div key={activity.id} className="timeline-item">
                          {leg && (
                            <div className="route-note">
                              {activeReq.transport === "walking" ? (
                                <Footprints size={13} />
                              ) : (
                                <TrainFront size={13} />
                              )}
                              <span>
                                {activeReq.transport === "walking"
                                  ? "步行"
                                  : "公共交通估计"}{" "}
                                {leg.duration_minutes} 分钟
                              </span>
                              <span>含 {10} 分钟衔接余量</span>
                            </div>
                          )}
                          <article
                            className={`activity-card ${selectedPlace === activity.place_id ? "highlighted" : ""}`}
                            onClick={() => setSelectedPlace(activity.place_id)}
                            tabIndex={0}
                            onFocus={() => setSelectedPlace(activity.place_id)}
                          >
                            <div className="timeline-number">
                              {String(index + 1).padStart(2, "0")}
                            </div>
                            <div className="activity-top">
                              <span className="activity-time">
                                {activity.start} — {activity.end}
                              </span>
                              {activity.locked && (
                                <span className="locked-badge">
                                  <LockKeyhole size={11} />
                                  已锁定
                                </span>
                              )}
                              <span
                                className={`category-tag ${place?.category}`}
                              >
                                {place?.indoor ? "室内为主" : "户外探索"}
                              </span>
                            </div>
                            <h3>{place?.name || activity.place_id}</h3>
                            <p className="activity-description">
                              {place?.description}
                            </p>
                            <div className="activity-bottom">
                              <span>
                                <Sparkles size={12} />
                                {activity.reason}
                              </span>
                              <div className="activity-actions">
                                <button
                                  aria-label={`${activity.locked ? "解锁" : "锁定"}${place?.name}`}
                                  title={
                                    activity.locked ? "解锁活动" : "锁定活动"
                                  }
                                  disabled={
                                    busy ||
                                    !!proposal ||
                                    !trip?.current_version ||
                                    activity.id.includes("-fixed")
                                  }
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    void adjust(
                                      activity.locked ? "unlock" : "lock",
                                      activity,
                                    );
                                  }}
                                >
                                  {activity.locked ? (
                                    <UnlockKeyhole size={14} />
                                  ) : (
                                    <LockKeyhole size={14} />
                                  )}
                                </button>
                                <button
                                  aria-label={`移除${place?.name}`}
                                  title="移除可选活动"
                                  disabled={
                                    busy ||
                                    !!proposal ||
                                    activity.locked ||
                                    activeReq.must_visit.includes(
                                      activity.place_id,
                                    ) ||
                                    !trip?.current_version
                                  }
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    void adjust("remove", activity);
                                  }}
                                >
                                  <Trash2 size={14} />
                                </button>
                              </div>
                            </div>
                            <details
                              className="evidence"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <summary>查看信息依据与限制</summary>
                              <p>{place?.evidence_note}</p>
                              <p>{leg?.evidence}</p>
                            </details>
                          </article>
                        </div>
                      );
                    })}
                    {!!day.activities.length && (
                      <>
                        <div className="route-note return-note">
                          <ArrowDown size={13} />
                          返回住处 · 路途约{" "}
                          {day.legs.at(-1)?.destination_id === "hotel"
                            ? day.legs.at(-1)?.duration_minutes
                            : "未知"}{" "}
                          分钟
                        </div>
                        <div className="hotel-marker end">
                          <span className="small-dot" />
                          <div>
                            <strong>回去休息，好好结束这一天</strong>
                            <p>
                              计划 {activeReq.day_end.slice(0, 5)} 前返回 ·
                              以实际交通为准
                            </p>
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                </>
              ) : (
                <div className="empty-state">
                  <div className="empty-art">
                    <Compass size={58} strokeWidth={1} />
                    <span className="empty-leaf">
                      <Leaf size={24} />
                    </span>
                  </div>
                  <span className="eyebrow">A GOOD DAY STARTS HERE</span>
                  <h3>有想去的地方，就有出发的理由。</h3>
                  <p>
                    选几个心仪的地点，留一点自由时间。
                    <br />
                    第一份行程，从左边开始。
                  </p>
                  <div className="empty-chips">
                    <span>01 整理需求</span>
                    <ChevronRight size={12} />
                    <span>02 安排路线</span>
                    <ChevronRight size={12} />
                    <span>03 随时调整</span>
                  </div>
                </div>
              )}
              {plan && (
                <section className="validation-card">
                  <div className="section-label">
                    <ShieldCheck size={16} />
                    <span>行程检查</span>
                    <span className={errors.length ? "error-text" : "muted"}>
                      {statusNames[plan.status]}
                    </span>
                  </div>
                  <ul>
                    {plan.checks.map((c, i) => (
                      <li
                        key={`${c.code}-${i}`}
                        className={c.severity === "error" ? "error-text" : ""}
                      >
                        <span className={`check-dot ${c.severity}`} />
                        {c.message}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </section>

            <aside className="insights-column">
              <RouteMap
                day={day}
                places={planPlaces}
                cityName={activeReq.city}
                hotel={activeReq.hotel}
                selected={selectedPlace}
                onSelect={setSelectedPlace}
              />
              <section className="weather-card">
                <div className="section-label">
                  <CloudSun size={17} />
                  <span>天气，留意一下</span>
                  <span className="tiny-tag">
                    {weather ? "逐日预报" : "待确认"}
                  </span>
                </div>
                <div className="weather-content">
                  <CloudSun size={34} strokeWidth={1.3} />
                  <div>
                    <strong>
                      {weather
                        ? `${weather.nighttemp}° — ${weather.daytemp}°`
                        : "还没有可靠预报"}
                    </strong>
                    <p>{weather ? weather.dayweather : "出发前再看一眼天气"}</p>
                  </div>
                </div>
                <p className="subtle-copy">
                  {plan?.weather.evidence ||
                    "天气未知时，行程会保留待确认提示。"}
                </p>
              </section>
              <section className="adjust-card">
                <div className="section-label">
                  <Sparkles size={16} />
                  <span>计划变了？一起调整</span>
                </div>
                <p>保留已确认的安排，只调整后面的路。</p>
                <div className="delay-control">
                  <label htmlFor="delay-minutes">晚出发</label>
                  <select
                    id="delay-minutes"
                    value={delay}
                    onChange={(e) => setDelay(Number(e.target.value))}
                  >
                    <option value={20}>20 分钟</option>
                    <option value={40}>40 分钟</option>
                    <option value={60}>60 分钟</option>
                  </select>
                  <button
                    aria-label="生成延误调整"
                    disabled={!trip?.current_version || busy || !!proposal}
                    onClick={() => void adjust("delay")}
                  >
                    <ArrowRight size={16} />
                  </button>
                </div>
                <button
                  className="indoor-button"
                  disabled={!trip?.current_version || busy || !!proposal}
                  onClick={() => void adjust("indoor")}
                >
                  <Umbrella size={15} />
                  这一天，优先安排室内
                  <ChevronRight size={14} />
                </button>
                {!trip?.current_version && (
                  <small>保存第一份行程后，即可尝试调整。</small>
                )}
              </section>
              <section className="trace-card">
                <div className="section-label">
                  <Navigation size={15} />
                  <span>这份安排如何产生</span>
                </div>
                {events.length ? (
                  <ol>
                    {events.map((e) => (
                      <li key={e.id}>
                        <CheckCircle2 size={13} />
                        <span>{e.message}</span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p>从需求、交通到约束检查，每一步都有记录。</p>
                )}
                {plan && (
                  <div className="trace-footer">
                    {plan.trace.length} 条工具记录 ·{" "}
                    {plan.planning_mode === "llm"
                      ? "模型选择候选"
                      : "规则演示排程"}
                  </div>
                )}
              </section>
            </aside>
          </div>
          <footer className="page-footer">
            <span>行间 · 给旅行一点余地</span>
            <span>计划是起点，风景在路上。</span>
          </footer>
        </div>
      </main>
      {settingsOpen && (
        <ApiSettings
          config={config}
          onSave={saveSettings}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {versions && (
        <div className="modal-backdrop" onClick={() => setVersions(null)}>
          <section
            className="history-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="history-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-title">
              <h2 id="history-title">每一步，都留有余地</h2>
              <button
                aria-label="关闭版本记录"
                onClick={() => setVersions(null)}
              >
                <X size={18} />
              </button>
            </div>
            <p>恢复会创建新版本，已有记录会保留。</p>
            {versions.map((v) => (
              <div className="version-row" key={v.number}>
                <span className="version-number">v{v.number}</span>
                <div>
                  <strong>{v.label}</strong>
                  <small>
                    {new Date(v.created_at).toLocaleString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                    })}
                  </small>
                </div>
                {v.number === trip?.current_version ? (
                  <span className="tiny-tag">当前版本</span>
                ) : (
                  <button
                    disabled={busy}
                    className="text-button"
                    onClick={() => void restore(v.number)}
                  >
                    <RotateCcw size={14} />
                    恢复
                  </button>
                )}
              </div>
            ))}
          </section>
        </div>
      )}
    </div>
  );
}
