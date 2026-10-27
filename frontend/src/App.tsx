import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowDown,
  ArrowRight,
  BookOpen,
  CarFront,
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
  UnlockKeyhole,
  X,
} from "lucide-react";
import {
  api,
  clearProjectConfig,
  loadProjectConfig,
  saveProjectConfig,
  waitForRun,
} from "./api";
import type {
  Action,
  Activity,
  Destination,
  Health,
  HotelChoice,
  Itinerary,
  Place,
  Proposal,
  Requirements,
  TravelProfile,
  Run,
  RunEvent,
  Trip,
  Version,
} from "./api";
import RouteMap from "./RouteMap";
import ApiSettings from "./ApiSettings";
import HotelPanel from "./HotelPanel";
import BoundaryEditor from "./BoundaryEditor";
import DestinationSelect from "./DestinationSelect";
import RecommendationBasis from "./RecommendationBasis";
import ItineraryChecks from "./ItineraryChecks";
import { nightMismatch, venuePresentation } from "./planningPresentation";
import TripHistory from "./TripHistory";
import TravelPreferences, {
  candidateRequestKey,
  emptyProfile,
} from "./TravelPreferences";
import { getBrowserConfig } from "./userConfig";
import type { UserAPIConfig } from "./userConfig";

const storedTripKey = "city-trip:last-trip";
const confirmedViewKey = "city-trip:confirmed-view";
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
  lodging_mode: "route_first",
  arrival: null,
  departure: null,
  day_windows: {},
  hotel: {
    name: "演示起点 · 湖滨",
    longitude: 120.163,
    latitude: 30.251,
    coordinate_system: "GCJ-02",
  },
  transport: "auto",
  use_web_guides: true,
  pace: "relaxed",
  must_visit: ["westlake", "gushan_museum"],
  appointments: [],
  note: "",
  profile: emptyProfile,
};
const statusNames: Record<Itinerary["status"], string> = {
  FEASIBLE: "已通过已知约束检查",
  NEEDS_CONFIRMATION: "有信息需要出行前确认",
  CONFLICT: "发现条件冲突",
  NO_SOLUTION_WITHIN_LIMIT: "暂未找到满足条件的方案",
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
  const [revisionMessage, setRevisionMessage] = useState("");
  const [revisionScope, setRevisionScope] = useState("auto");
  const [reselectPlaces, setReselectPlaces] = useState(false);
  const [showForm, setShowForm] = useState(true);
  const [config, setConfig] = useState<UserAPIConfig>(getBrowserConfig);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [configPath, setConfigPath] = useState(".local/api-config.json");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const destinationRequest = useRef(0);
  const [catalogRequestKey, setCatalogRequestKey] = useState<string | null>(
    null,
  );
  const [candidateNote, setCandidateNote] = useState("");
  const latestCandidateKey = useRef("");
  latestCandidateKey.current = candidateRequestKey(form);
  const catalogStale =
    !!form.catalog_id &&
    catalogRequestKey !== null &&
    catalogRequestKey !== candidateRequestKey(form);
  const plan = proposal?.itinerary || trip?.itinerary || null;
  const day = plan?.days.find((d) => d.day === selectedDay) || plan?.days[0];
  const activeReq = proposal?.requirements || trip?.requirements || form;
  const errors = plan?.checks.filter((c) => c.severity === "error") || [];
  const canApply =
    !!proposal &&
    errors.length === 0 &&
    !["CONFLICT", "NO_SOLUTION_WITHIN_LIMIT"].includes(
      proposal.itinerary.status,
    );
  const saveBlockers = errors.length
    ? Array.from(new Set(errors.map((check) => check.message)))
    : proposal && !canApply
      ? [statusNames[proposal.itinerary.status] || "本次调整尚未通过行程检查。"]
      : [];
  const totalActivities =
    plan?.days.reduce((n, d) => n + d.activities.length, 0) || 0;
  const transitMinutes =
    day?.legs.reduce((n, leg) => n + leg.duration_minutes, 0) || 0;
  const minute = (value: string) =>
    Number(value.slice(0, 2)) * 60 + Number(value.slice(3, 5));
  const startMinutes =
    day &&
    !day.activities.length &&
    day.day === activeReq.days &&
    activeReq.departure
      ? Math.max(
          0,
          minute(activeReq.departure.time) -
            activeReq.departure.buffer_minutes -
            (day.legs.at(-1)?.duration_minutes || 0) -
            10,
        )
      : Math.max(
          minute(
            activeReq.day_windows?.[String(day?.day || 1)]?.start ||
              activeReq.day_start,
          ) + (plan?.day_start_offsets?.[String(day?.day || 1)] || 0),
          (day?.day || 1) === 1 && activeReq.arrival
            ? minute(activeReq.arrival.time) + activeReq.arrival.buffer_minutes
            : 0,
        );
  const displayedStart = `${String(Math.floor(startMinutes / 60)).padStart(2, "0")}:${String(startMinutes % 60).padStart(2, "0")}`;
  const activeToolProvider = plan?.tool_provider || health?.tool_provider;
  const planPlaces = proposal?.places || trip?.places || places;
  const placeById = (id: string) => planPlaces.find((p) => p.id === id);
  const plannedPlaceIds = new Set(
    plan?.days.flatMap((d) => d.activities.map((a) => a.place_id)) || [],
  );
  const alternativeReasons =
    plan?.planning_decision?.recommendations.filter(
      (r) => !plannedPlaceIds.has(r.place_id),
    ) || [];

  const routeFirst = activeReq.lodging_mode === "route_first";
  const chosenHotel = (night: number) => {
    const stay = plan?.lodging?.nights.find((stay) => stay.night === night);
    return stay?.candidates.find((hotel) => hotel.id === stay.selected_id)
      ?.location;
  };
  const startHotel =
    day?.day === 1 && activeReq.arrival
      ? activeReq.arrival.location
      : routeFirst
        ? chosenHotel((day?.day || 1) - 1)
        : activeReq.hotel;
  const endHotel =
    day?.day === activeReq.days && activeReq.departure
      ? activeReq.departure.location
      : routeFirst
        ? chosenHotel(day?.day || 1)
        : activeReq.hotel;
  const catalogReady =
    config.tool_provider === "demo" ||
    (!!form.catalog_id && places.length > 0 && places.every((p) => !p.fixture));
  const fixedExampleAvailable = places.some((p) => p.id === "gushan_museum");

  async function loadTrip(id: string) {
    const saved = await api<Trip>(`/trips/${id}`);
    const requirements = {
      ...initial,
      ...saved.requirements,
      lodging_mode: saved.requirements.lodging_mode || "fixed",
      city_id: saved.requirements.city_id ?? 19598,
    };
    setTrip({ ...saved, requirements });
    setForm({ ...requirements, transport: "auto" });
    setPlaces(saved.places);
    setCatalogRequestKey(candidateRequestKey(requirements));
    setAppointmentOn(saved.requirements.appointments.length > 0);
    return saved;
  }

  function changeRegion(country: string, state: string) {
    destinationRequest.current++;
    setPlaces([]);
    setCatalogRequestKey(null);
    setCandidateNote("");
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
      arrival: null,
      departure: null,
      day_windows: {},
      place_days: {},
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
        arrival: null,
        departure: null,
        day_windows: {},
        place_days: {},
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
    const requestKey = candidateRequestKey(form);
    const keepIds = form.catalog_id
      ? Array.from(
          new Set([
            ...form.must_visit,
            ...(form.appointments || []).map((a) => a.place_id),
            ...Object.keys(form.place_days || {}),
          ]),
        )
      : [];
    try {
      const result = await api<{
        catalog_id: string;
        places: Place[];
        center: Requirements["hotel"];
        profile: TravelProfile;
        note: string;
      }>("/places/recommendations", {
        city_id: form.city_id,
        start_date: form.start_date,
        days: form.days,
        pace: form.pace,
        profile: form.profile || emptyProfile,
        note: form.note,
        catalog_id: form.catalog_id,
        keep_ids: keepIds,
        ...(trip &&
        trip.requirements.city_id === form.city_id &&
        form.catalog_id
          ? { source_trip_id: trip.id, base_version: trip.current_version }
          : {}),
      });
      if (
        ticket !== destinationRequest.current ||
        requestKey !== latestCandidateKey.current
      )
        return;
      setPlaces(result.places);
      setCatalogRequestKey(requestKey);
      setCandidateNote(result.note);
      setForm((f) => ({
        ...f,
        catalog_id: result.catalog_id,
        must_visit: f.must_visit.filter((id) =>
          result.places.some((p) => p.id === id),
        ),
      }));
      setNotice(
        "已按旅行需求更新候选，保留已选择的同城地点。可查看推荐理由与待核实信息。",
      );
    } catch (e) {
      if (
        ticket === destinationRequest.current &&
        requestKey === latestCandidateKey.current
      )
        setError(e instanceof Error ? e.message : "地点查询失败");
    } finally {
      if (ticket === destinationRequest.current) setCatalogLoading(false);
    }
  }

  async function saveSettings(next: UserAPIConfig, clear = false) {
    const refreshMap =
      next.amap_js_key !== config.amap_js_key ||
      next.amap_security_code !== config.amap_security_code;
    const saved = clear
      ? await clearProjectConfig()
      : await saveProjectConfig(next);
    const info = await api<Health>("/health");
    setConfig(saved.config);
    setConfigPath(saved.path);
    setHealth(info);
    setSettingsOpen(false);
    setNotice(
      clear
        ? "已清除项目中的 API 配置文件，切回演示模式。"
        : "API 设置已保存到项目本地文件，下次启动自动读取。真实地图模式请先查询城市地点。",
    );
    if (refreshMap) window.location.reload();
  }

  useEffect(() => {
    let alive = true;
    async function init() {
      try {
        const savedConfig = await loadProjectConfig();
        if (!alive) return;
        setConfig(savedConfig.config);
        setConfigPath(savedConfig.path);
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
            if (
              saved.latest_run_id &&
              localStorage.getItem(confirmedViewKey) !== id
            ) {
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
                localStorage.removeItem(confirmedViewKey);
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
        if (alive) {
          setConfig(getBrowserConfig());
          setError(e instanceof Error ? e.message : "无法连接本地服务");
        }
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
    localStorage.removeItem(confirmedViewKey);
    setBusy(true);
    setProposal(null);
    setEvents([]);
    try {
      if (config.tool_provider === "amap" && catalogStale)
        throw new Error("旅行需求已变化，请先更新候选地点。");
      const req = {
        ...form,
        transport: "auto" as const,
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
            : fixedExampleAvailable
              ? []
              : form.appointments,
      };
      if (
        (req.arrival && !req.arrival.location.name.trim()) ||
        (req.departure && !req.departure.location.name.trim())
      ) {
        throw new Error("请先确认抵离地点的名称和实际位置，再生成行程。");
      }
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
    localStorage.removeItem(confirmedViewKey);
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
          delay_minutes: 40,
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

  async function reviseFromText() {
    if (!trip || proposal || !revisionMessage.trim()) return;
    localStorage.removeItem(confirmedViewKey);
    setBusy(true);
    setError("");
    setNotice("");
    setEvents([]);
    try {
      const { run_id } = await api<{ run_id: string }>(
        `/trips/${trip.id}/revisions`,
        {
          base_version: trip.current_version,
          message: revisionMessage.trim(),
          scope: revisionScope === "auto" ? null : revisionScope,
          reselect: reselectPlaces,
        },
      );
      const run = await waitForRun(run_id, setEvents);
      setProposal(run.proposal);
    } catch (error) {
      setError(error instanceof Error ? error.message : "调整失败");
    } finally {
      setBusy(false);
    }
  }

  async function chooseHotel(choice: HotelChoice) {
    if (!trip || proposal || !trip.current_version) return;
    localStorage.removeItem(confirmedViewKey);
    setBusy(true);
    setError("");
    setNotice("");
    setEvents([]);
    try {
      const { run_id } = await api<{ run_id: string }>(
        `/trips/${trip.id}/hotel-selections`,
        {
          base_version: trip.current_version,
          ...choice,
        },
      );
      const run = await waitForRun(run_id, setEvents);
      setProposal(run.proposal);
    } catch (e) {
      setError(e instanceof Error ? e.message : "酒店选择失败");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!trip || !proposal) return;
    localStorage.removeItem(confirmedViewKey);
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
    localStorage.removeItem(confirmedViewKey);
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
    localStorage.removeItem(confirmedViewKey);
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
    localStorage.removeItem(confirmedViewKey);
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
    setSelectedPlace(null);
    setVersions(null);
    setRevisionMessage("");
    setCandidateNote("");
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

  async function openHistoricalTrip(id: string) {
    destinationRequest.current++;
    await loadTrip(id);
    localStorage.setItem(storedTripKey, id);
    localStorage.setItem(confirmedViewKey, id);
    setProposal(null);
    setEvents([]);
    setError("");
    setNotice("已打开确认保存的行程。");
    setSelectedDay(1);
    setSelectedPlace(null);
    setVersions(null);
    setRevisionMessage("");
    setCandidateNote("");
    setShowForm(false);
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
            className="text-button history-trigger"
            disabled={busy || loading || catalogLoading}
            onClick={() => setHistoryOpen(true)}
          >
            <History size={16} />
            历史行程
          </button>
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
            <TravelPreferences
              form={form}
              onChange={setForm}
              disabled={busy || catalogLoading}
            />
            {config.tool_provider === "amap" && (
              <div className="catalog-query">
                <p>
                  {form.country_code === "CN"
                    ? "按同行人、兴趣与步行偏好查询当前城市的候选地点。"
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
                  {form.catalog_id ? "按新需求更新候选" : "查询城市真实地点"}
                </button>
              </div>
            )}
            {catalogStale && (
              <p role="status" className="candidate-warning">
                旅行需求已变化，请更新候选；已选必去地点会保留。
              </p>
            )}
            {candidateNote && <p className="field-hint">{candidateNote}</p>}
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
                <label htmlFor="day-end">
                  {form.lodging_mode === "route_first"
                    ? "当天结束"
                    : "返回住处"}
                </label>
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
              <BoundaryEditor
                key={`arrival-${form.city_id}`}
                kind="arrival"
                city={form.city}
                center={form.hotel}
                value={form.arrival || null}
                onChange={(arrival) => setForm({ ...form, arrival })}
              />
              <BoundaryEditor
                key={`departure-${form.city_id}`}
                kind="departure"
                city={form.city}
                center={form.hotel}
                value={form.departure || null}
                onChange={(departure) => setForm({ ...form, departure })}
              />
              <label htmlFor="lodging-mode">住宿安排</label>
              <select
                id="lodging-mode"
                value={form.lodging_mode || "fixed"}
                onChange={(e) =>
                  setForm({
                    ...form,
                    lodging_mode: e.target.value as "fixed" | "route_first",
                  })
                }
              >
                <option value="route_first">先规划路线，再选择酒店</option>
                <option value="fixed">已有固定住处，每天往返</option>
              </select>
              {form.lodging_mode === "route_first" && (
                <p className="field-hint">
                  先安排游玩地点，再根据每晚前后路线推荐住宿。建议同时填写首日抵达与末日返程信息。
                </p>
              )}
            </div>
            {form.lodging_mode !== "route_first" && (
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
            )}
            <fieldset className="travel-research-options">
              <legend>攻略与交通建议</legend>
              <label>
                <input
                  type="checkbox"
                  checked={form.use_web_guides}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      use_web_guides: e.target.checked,
                      transport: "auto",
                    })
                  }
                />
                参考网上攻略
              </label>
              <p>
                结合攻略和地点距离，逐段建议步行、打车或公交。高德查询会排队并复用缓存，避免并发超限。
              </p>
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
              <legend>
                一定想去{" "}
                <span className="legend-hint">
                  {form.must_visit.length} 个地点
                </span>
              </legend>
              <div className="place-options">
                {places.map((p) => (
                  <div key={p.id} className="candidate-option">
                    <label
                      className={
                        form.must_visit.includes(p.id) ? "checked" : ""
                      }
                    >
                      <input
                        type="checkbox"
                        checked={form.must_visit.includes(p.id)}
                        onChange={() => toggleMust(p.id)}
                      />
                      <span className="candidate-copy">
                        <strong>{p.name}</strong>
                        {p.candidate_match && (
                          <small>
                            程序匹配：{p.candidate_match.reasons.join(" · ")}
                          </small>
                        )}
                        {p.traits?.duration_min && (
                          <small>
                            建议 {p.traits.duration_min}～
                            {p.traits.duration_max} 分钟（估算）
                          </small>
                        )}
                        {p.source_url && (
                          <a
                            href={p.source_url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                          >
                            查看地点来源
                          </a>
                        )}
                        {p.traits?.unknowns?.length ? (
                          <small>待核实：{p.traits.unknowns.join("、")}</small>
                        ) : null}
                      </span>
                      {form.must_visit.includes(p.id) && <Check size={14} />}
                    </label>
                    <RecommendationBasis place={p} compact />
                  </div>
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
                !catalogReady ||
                (config.tool_provider === "amap" && catalogStale)
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
                  ? `${activeReq.start_date.replaceAll("-", ".")} 出发 · ${totalActivities} 个活动 · ${routeFirst && activeReq.days > 1 ? (plan.lodging?.nights.every((n) => n.selected_id) && plan.lodging.nights.length === activeReq.days - 1 ? "住宿已选择" : "路线草案，住宿待选") : "分段交通建议"}`
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
                  {proposal.label.includes("住宿调整")
                    ? "本次更新住宿与相邻两天交通，并复核活动安排。请检查时间与顺序变化后保存。"
                    : trip?.current_version
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
                {saveBlockers.length > 0 && (
                  <div
                    className="save-blockers"
                    id="save-blockers"
                    role="alert"
                  >
                    <strong>暂时无法保存</strong>
                    <ul>
                      {saveBlockers.map((message) => (
                        <li key={message}>{message}</li>
                      ))}
                    </ul>
                    <p>
                      已保存的行程未改变。可取消本次调整，修改要求后重新生成。
                    </p>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() =>
                        document
                          .getElementById("itinerary-checks")
                          ?.scrollIntoView({
                            behavior: "smooth",
                            block: "center",
                          })
                      }
                    >
                      查看完整行程检查 ↓
                    </button>
                  </div>
                )}
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
                  aria-describedby={
                    saveBlockers.length ? "save-blockers" : undefined
                  }
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
                    <div
                      className={
                        (day.day === 1 && activeReq.arrival) ||
                        (day.day === activeReq.days && activeReq.departure)
                          ? "boundary-day"
                          : "empty-small"
                      }
                    >
                      {(day.day === 1 && activeReq.arrival) ||
                      (day.day === activeReq.days && activeReq.departure)
                        ? day.legs.length
                          ? "当天仅安排抵离接驳与休息。"
                          : "当天仅安排抵离接驳与休息；尚未选择住宿的路段将在选酒店后补齐。"
                        : "当前条件下没有排入活动。请查看下方检查结果，或增加可用时间。"}
                    </div>
                  )}
                  {plan.planning_decision &&
                    plan.planning_decision.source !== "legacy" && (
                      <div className="planning-decision" role="note">
                        <strong>
                          {plan.planning_decision.source === "model_schedule"
                            ? "本次由模型安排地点与具体时间"
                            : plan.planning_decision.source === "model_days"
                              ? "本次由模型选点与分天"
                              : plan.planning_decision.source === "model_order"
                                ? "本次由模型排序、程序分天"
                                : "本次使用程序规则排程"}
                        </strong>
                        <span>
                          主规划模型请求 {plan.planning_decision.model_calls} 次
                        </span>
                        {plan.planning_decision.notes.map((note, i) => (
                          <p key={i}>{note}</p>
                        ))}
                        {alternativeReasons.length > 0 && (
                          <details className="alternative-reasons">
                            <summary>
                              未排入的候选与取舍（{alternativeReasons.length}）
                            </summary>
                            {alternativeReasons.map((reason) => (
                              <div key={reason.place_id}>
                                <strong>
                                  {placeById(reason.place_id)?.name ||
                                    reason.place_id}
                                </strong>
                                <RecommendationBasis
                                  place={placeById(reason.place_id)}
                                  recommendation={reason}
                                  sources={plan.guides?.sources || []}
                                />
                              </div>
                            ))}
                          </details>
                        )}
                      </div>
                    )}
                  {(day.weather_note || !!day.breaks?.length) && (
                    <section
                      className="day-arrangements"
                      aria-label="当日天气与休息安排"
                    >
                      {day.weather_note && (
                        <p className="day-weather-note">
                          <CloudSun size={16} />
                          <span>
                            <strong>天气安排依据</strong>
                            {day.weather_note}
                          </span>
                        </p>
                      )}
                      {!!day.breaks?.length && (
                        <>
                          <h3>
                            <Clock3 size={15} />
                            用餐、休息与留白
                          </h3>
                          <ul>
                            {day.breaks.map((block, index) => (
                              <li key={index}>
                                <time>
                                  {block.start} — {block.end}
                                </time>
                                <span>
                                  {block.kind === "meal"
                                    ? "用餐"
                                    : block.kind === "rest"
                                      ? "休息"
                                      : "自由安排"}{" "}
                                  · {block.reason}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                    </section>
                  )}
                  <div className="timeline">
                    <div className="hotel-marker">
                      <span className="small-dot" />
                      <div>
                        <strong>
                          {startHotel?.name || "从当天第一个景点开始"}
                        </strong>
                        <p>
                          {startHotel
                            ? day.day === 1 && activeReq.arrival
                              ? `抵达后预留 ${activeReq.arrival.buffer_minutes} 分钟出站`
                              : "从已选住处出发"
                            : "住宿及抵达交通待补齐"}
                        </p>
                      </div>
                    </div>
                    {day.activities.map((activity, index) => {
                      const place = placeById(activity.place_id);
                      const recommendation =
                        plan.planning_decision?.recommendations.find(
                          (r) => r.place_id === activity.place_id,
                        );
                      const assessment =
                        plan.planning_decision?.place_assessments?.find(
                          (a) => a.place_id === activity.place_id,
                        );
                      const venue = venuePresentation(place, assessment);
                      const leg = day.legs.find(
                        (leg) => leg.destination_id === activity.place_id,
                      );
                      return (
                        <div key={activity.id} className="timeline-item">
                          {leg && (
                            <div className="route-note">
                              {leg.mode === "walking" ? (
                                <Footprints size={13} />
                              ) : leg.mode === "driving" ? (
                                <CarFront size={13} />
                              ) : (
                                <TrainFront size={13} />
                              )}
                              <span>
                                {leg.mode === "walking"
                                  ? "步行"
                                  : leg.mode === "driving"
                                    ? "打车 / 自驾"
                                    : leg.mode === "transit"
                                      ? "公交 / 地铁"
                                      : "交通"}{" "}
                                {leg.duration_minutes} 分钟
                              </span>
                              <span>含 {10} 分钟衔接余量</span>
                              {leg.recommendation && (
                                <p className="leg-recommendation">
                                  {leg.recommendation}
                                  {leg.route_status !== "queried" &&
                                    "（估时，待出发前确认）"}
                                </p>
                              )}
                              {leg.source_ids?.map((id) => {
                                const source = plan.guides?.sources.find(
                                  (s) => s.id === id,
                                );
                                return source ? (
                                  <a
                                    key={id}
                                    href={source.url}
                                    target="_blank"
                                    rel="noreferrer"
                                  >
                                    攻略依据 ↗
                                  </a>
                                ) : null;
                              })}
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
                                {venue.label}
                              </span>
                            </div>
                            <h3>{place?.name || activity.place_id}</h3>
                            {activeReq.place_days?.[activity.place_id] && (
                              <span className="assigned-day-badge">
                                你指定在第{" "}
                                {activeReq.place_days[activity.place_id]} 天
                              </span>
                            )}
                            <p className="activity-description">
                              {place?.description}
                            </p>
                            <RecommendationBasis
                              place={place}
                              recommendation={recommendation}
                              activity={activity}
                              assessment={assessment}
                              review={plan.planning_decision?.quality_review}
                              sources={plan.guides?.sources || []}
                            />
                            <div className="activity-bottom">
                              <span>
                                <Sparkles size={12} />
                                {nightMismatch(
                                  activity,
                                  recommendation,
                                  plan.planning_decision?.quality_review,
                                )
                                  ? "当前时段尚未落实夜景目标，需调整时间或重新取舍。"
                                  : activity.reason}
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
                              {place?.source_url && (
                                <a
                                  href={place.source_url}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  查看地点来源 ↗
                                </a>
                              )}
                              <p>{leg?.evidence}</p>
                            </details>
                          </article>
                        </div>
                      );
                    })}
                    {(!!day.activities.length || !!day.legs.length) &&
                      endHotel && (
                        <>
                          <div className="route-note return-note">
                            <ArrowDown size={13} />
                            {day.day === activeReq.days && activeReq.departure
                              ? "前往返程地点"
                              : "返回住处"}{" "}
                            · 路途约{" "}
                            {day.legs.at(-1)?.duration_minutes ?? "未知"} 分钟
                          </div>
                          <div className="hotel-marker end">
                            <span className="small-dot" />
                            <div>
                              <strong>{endHotel.name}</strong>
                              <p>
                                {day.day === activeReq.days &&
                                activeReq.departure
                                  ? `${activeReq.departure.time.slice(0, 5)} 返程，提前 ${activeReq.departure.buffer_minutes} 分钟到站`
                                  : `计划 ${activeReq.day_end.slice(0, 5)} 前返回 · 以实际交通为准`}
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
              {plan?.revision_note && (
                <section className="revision-review" aria-label="调整说明">
                  <strong>
                    {plan.revision_status === "model_reviewed"
                      ? "主 Agent 的调整判断"
                      : "本次调整说明"}
                  </strong>
                  <p>{plan.revision_note}</p>
                  {!!plan.revision_rounds && (
                    <p>经过 {plan.revision_rounds} 轮协调与行程检查。</p>
                  )}
                  {!!plan.revision_changes?.length && (
                    <ul className="revision-change-reasons">
                      {plan.revision_changes.map((change, index) => (
                        <li key={index}>{change}</li>
                      ))}
                    </ul>
                  )}
                </section>
              )}
              {plan && (
                <ItineraryChecks
                  plan={plan}
                  statusLabel={statusNames[plan.status]}
                />
              )}
              {plan?.lodging && (
                <HotelPanel
                  lodging={plan.lodging}
                  places={planPlaces}
                  boundaryLocations={{
                    arrival: activeReq.arrival?.location,
                    departure: activeReq.departure?.location,
                  }}
                  disabled={busy || !!proposal || !trip?.current_version}
                  needsSave={!!proposal || !trip?.current_version}
                  onChoose={(choice) => void chooseHotel(choice)}
                />
              )}
            </section>

            <aside className="insights-column">
              <RouteMap
                day={day}
                places={planPlaces}
                cityName={activeReq.city}
                hotel={startHotel}
                endHotel={endHotel}
                startLabel={
                  day?.day === 1 && activeReq.arrival ? "抵达地点" : "出发住处"
                }
                endLabel={
                  day?.day === activeReq.days && activeReq.departure
                    ? "返程地点"
                    : "住宿"
                }
                selected={selectedPlace}
                onSelect={setSelectedPlace}
              />
              {plan?.guides && (
                <section className="guide-card">
                  <div className="section-label">
                    <BookOpen size={15} />
                    <span>攻略参考</span>
                    <span className="tiny-tag">
                      {plan.guides.sources.length} 个来源
                    </span>
                  </div>
                  <p className="guide-note">{plan.guides.note}</p>
                  {plan.guides.discovery_note && (
                    <div
                      className="guide-discovery-note"
                      role="note"
                      aria-label="攻略补充候选"
                    >
                      <p>{plan.guides.discovery_note}</p>
                      {!!plan.guides.discovered_place_ids?.length && (
                        <p>
                          新增候选：
                          {plan.guides.discovered_place_ids
                            .map((id) => placeById(id)?.name || id)
                            .join("、")}
                        </p>
                      )}
                    </div>
                  )}
                  {plan.guides.sources.map((source, index) => (
                    <a
                      className="guide-source"
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      key={source.id}
                    >
                      <strong>
                        {index + 1}. {source.title} ↗
                      </strong>
                      <span>
                        {source.content_kind === "page_excerpt"
                          ? "已读取页面片段"
                          : "搜索摘要参考"}{" "}
                        · {source.url.replace(/^https?:\/\//, "").split("/")[0]}
                      </span>
                    </a>
                  ))}
                </section>
              )}
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
                    <p>
                      {weather
                        ? `白天${weather.dayweather}${weather.nightweather ? ` · 夜间${weather.nightweather}` : ""}`
                        : "出发前再看一眼天气"}
                    </p>
                    {weather?.reporttime && (
                      <small>预报发布：{weather.reporttime}</small>
                    )}
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
                <p>
                  说说想去哪里、哪天去。排不下时会协调其他活动，已选酒店与锁定预约会保留。
                </p>
                <label htmlFor="revision-message">想怎样调整？</label>
                <textarea
                  id="revision-message"
                  rows={4}
                  maxLength={1500}
                  value={revisionMessage}
                  placeholder="例如：第2天晚出发40分钟，把博物馆移到第3天，酒店保持不变。"
                  onChange={(event) => setRevisionMessage(event.target.value)}
                />
                <label htmlFor="revision-scope">协调范围</label>
                <select
                  id="revision-scope"
                  value={revisionScope}
                  onChange={(event) => setRevisionScope(event.target.value)}
                >
                  <option value="auto">按需求协调相关日期</option>
                  <option value="local">
                    只执行指定修改，其他活动保持不变
                  </option>
                  <option value="all_days">允许协调整个行程</option>
                </select>
                <label className="reselect-option">
                  <input
                    type="checkbox"
                    checked={reselectPlaces}
                    onChange={(event) =>
                      setReselectPlaces(event.target.checked)
                    }
                  />
                  允许重新挑选普通推荐地点
                </label>
                <p className="field-hint">
                  可按需补查新景点。明确指定的地点、日期和必去要求会保留；新增与移除均会先展示。
                </p>
                <div className="revision-examples">
                  <button
                    type="button"
                    onClick={() =>
                      setRevisionMessage(`第${selectedDay}天晚出发40分钟`)
                    }
                  >
                    晚点出发
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      setRevisionMessage(`第${selectedDay}天优先室内活动`)
                    }
                  >
                    优先室内
                  </button>
                </div>
                <button
                  className="outline-button"
                  aria-label="生成行程调整"
                  disabled={
                    !trip?.current_version ||
                    busy ||
                    !!proposal ||
                    !revisionMessage.trim()
                  }
                  onClick={() => void reviseFromText()}
                >
                  <ArrowRight size={15} />
                  生成调整预览
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
          configPath={configPath}
          config={config}
          onSave={saveSettings}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {historyOpen && (
        <TripHistory
          onClose={() => setHistoryOpen(false)}
          onOpen={openHistoricalTrip}
          onDeleted={(id) => {
            if (trip?.id === id) reset();
          }}
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
