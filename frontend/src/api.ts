import {
  clearLegacyBrowserConfig,
  emptyConfig,
  getLegacyBrowserConfig,
  requestConfigHeader,
  setRuntimeConfig,
} from "./userConfig";
import type { UserAPIConfig } from "./userConfig";

export interface Location {
  name: string;
  longitude: number;
  latitude: number;
  coordinate_system: "GCJ-02" | "WGS84";
}
export interface Place extends Location {
  id: string;
  subtitle: string;
  category: "nature" | "culture" | "coffee" | "city";
  indoor: boolean;
  duration_minutes: number;
  opening: string | null;
  closing: string | null;
  description: string;
  source_url: string;
  evidence_note: string;
  fixture: boolean;
  traits?: {
    themes: string[];
    indoor_outdoor: "indoor" | "outdoor" | "mixed" | "unknown";
    duration_min: number | null;
    duration_max: number | null;
    audience_notes: string[];
    unknowns: string[];
    evidence: {
      field: string;
      value: string;
      kind: string;
      source_url: string;
      observed_at: string;
      excerpt: string;
    }[];
  };
  candidate_match?: {
    score: number;
    relevance: number;
    diversity_penalty: number;
    detour_penalty: number;
    reasons: string[];
  } | null;
}
export interface TravelProfile {
  companions: "unspecified" | "solo" | "friends" | "parents" | "children";
  themes: (
    | "history"
    | "architecture"
    | "night"
    | "photo"
    | "nature"
    | "food"
    | "coffee"
    | "culture"
    | "city"
  )[];
  walking: "normal" | "less";
  excluded_keywords: string[];
}
export interface Requirements {
  city: string;
  country_code: string;
  country: string;
  state_id: string;
  province: string;
  city_id: number | null;
  timezone: string;
  catalog_id: string | null;
  start_date: string;
  days: number;
  day_start: string;
  day_end: string;
  hotel: Location;
  lodging_mode?: "fixed" | "route_first";
  arrival?: TravelBoundary | null;
  departure?: TravelBoundary | null;
  day_windows?: Record<string, { start: string; end: string }>;
  place_days?: Record<string, number>;
  transport: "auto" | "transit" | "walking";
  use_web_guides: boolean;
  pace: "relaxed" | "balanced";
  must_visit: string[];
  appointments: { place_id: string; day: number; start: string; end: string }[];
  note: string;
  profile?: TravelProfile;
}
export interface TravelBoundary {
  location: Location;
  time: string;
  buffer_minutes: number;
}
export interface Activity {
  id: string;
  place_id: string;
  day: number;
  start: string;
  end: string;
  locked: boolean;
  reason: string;
  time_intent?: "any" | "daytime" | "sunset" | "evening" | "night";
}
export interface VenueAssessment {
  place_id: string;
  indoor_outdoor: "indoor" | "outdoor" | "mixed" | "unknown";
  confidence: "high" | "medium" | "low";
  reason: string;
  basis: "model";
}
export interface RouteLeg {
  origin_id: string;
  destination_id: string;
  duration_minutes: number;
  distance_meters: number;
  walking_meters: number | null;
  provider: string;
  estimated: boolean;
  evidence: string;
  mode?: "walking" | "transit" | "driving" | "unknown";
  recommendation?: string;
  source_ids?: string[];
  route_status?: "queried" | "estimated";
}
export interface DayPlan {
  day: number;
  date: string;
  activities: Activity[];
  legs: RouteLeg[];
  breaks?: {
    start: string;
    end: string;
    kind: "meal" | "rest" | "free";
    reason: string;
  }[];
  weather_note?: string;
}
export interface Itinerary {
  status:
    "FEASIBLE" | "NEEDS_CONFIRMATION" | "CONFLICT" | "NO_SOLUTION_WITHIN_LIMIT";
  days: DayPlan[];
  checks: {
    code: string;
    severity: string;
    message: string;
    activity_id: string | null;
  }[];
  weather: {
    status: string;
    data: {
      days?: {
        date: string;
        dayweather: string;
        nightweather?: string;
        reporttime?: string;
        daytemp: string;
        nighttemp: string;
      }[];
    };
    evidence: string;
    provider: string;
    error: string | null;
  };
  summary: string;
  trace: { tool: string; status: string; provider?: string }[];
  planning_mode: string;
  tool_provider: string;
  planning_decision?: {
    source:
      "model_schedule" | "model_days" | "model_order" | "rules" | "legacy";
    model_calls: number;
    notes: string[];
    recommendations: PlaceRecommendation[];
    review_rounds?: number;
    review_outcome?:
      "not_requested" | "applied" | "unchanged" | "failed" | "budget_exhausted";
    selected_attempt?: number;
    weather_provided?: boolean;
    place_assessments?: VenueAssessment[];
    quality_review?: {
      status: "not_requested" | "passed" | "changes_requested" | "unavailable";
      summary: string;
      days: {
        day: number;
        evening: "scheduled" | "rest_justified" | "not_applicable" | "missing";
        pace: "appropriate" | "rest_justified" | "too_sparse" | "too_dense";
        explanation: string;
      }[];
      issues: {
        category: string;
        day: number;
        place_ids: string[];
        alternative_ids: string[];
        evidence: string;
        suggestion: string;
      }[];
      failure_code?: string;
      overall_score?: number | null;
    };
    review_failure?: {
      code: string;
      message: string;
      retryable: boolean;
      details?: { code: string; field: string; message: string }[];
    } | null;
  };
  day_start_offsets: Record<string, number>;
  lodging?: HotelResearch;
  revision_note?: string;
  revision_rounds?: number;
  revision_changes?: string[];
  revision_status?:
    "not_requested" | "model_reviewed" | "rules_only" | "unavailable";
  guides?: {
    status: "ok" | "partial" | "unavailable" | "not_requested";
    note: string;
    fetched_at: string;
    sources: { id: string; title: string; url: string; content_kind: string }[];
    discovered_place_ids?: string[];
    discovery_note?: string;
  };
}
export interface PlaceRecommendation {
  place_id: string;
  reason: string;
  role: "landmark" | "interest" | "neighborhood" | "rest" | "alternative";
  source_ids: string[];
  tradeoff: string;
  basis: "model" | "rule";
}
export interface HotelCandidate {
  id: string;
  location: Location;
  address: string;
  provider: "amap" | "demo" | "user";
  source_url: string;
  observed_at: string;
  evening_distance_meters: number;
  morning_distance_meters: number;
  evening_minutes: number;
  morning_minutes: number;
  reason: string;
  price_note: string;
}
export interface HotelNight {
  night: number;
  checkin: string;
  checkout: string;
  last_place_id: string;
  next_place_id: string;
  candidates: HotelCandidate[];
  selected_id: string | null;
  note: string;
}
export interface HotelResearch {
  status: "not_requested" | "not_needed" | "ready" | "partial" | "unavailable";
  nights: HotelNight[];
  note: string;
  analysis_status: "not_requested" | "ok" | "unavailable";
}
export interface HotelChoice {
  night: number;
  candidate_id?: string;
  custom_location?: Location;
}
export interface Proposal {
  id: string;
  base_version: number;
  itinerary: Itinerary;
  requirements?: Requirements | null;
  places?: Place[];
  label: string;
  applied_version: number | null;
  dismissed: boolean;
}
export interface Trip {
  id: string;
  requirements: Requirements;
  current_version: number;
  itinerary: Itinerary | null;
  latest_run_id: string | null;
  places: Place[];
}
export interface RunEvent {
  id: number;
  stage: string;
  message: string;
  at: string;
}
export interface Run {
  id: string;
  status: string;
  events: RunEvent[];
  error: string | null;
  proposal: Proposal | null;
}
export interface Health {
  tool_provider: string;
  planning_mode: string;
  catalog_mode: string;
  llm_configured: boolean;
}
export interface Version {
  number: number;
  label: string;
  created_at: string;
}
export type Action = "delay" | "lock" | "unlock" | "remove" | "indoor";

export async function api<T>(
  path: string,
  body?: unknown,
  config?: UserAPIConfig,
  options?: { signal?: AbortSignal; method?: "GET" | "POST" | "DELETE" },
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: options?.method || (body === undefined ? "GET" : "POST"),
    headers: {
      ...(config ? { "X-Travel-Config": requestConfigHeader(config) } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: options?.signal,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e: { msg: string }) => e.msg).join("；")
          : "服务暂时不可用，请检查后端是否已启动",
    );
  }
  return payload as T;
}

interface ProjectConfig {
  config: UserAPIConfig;
  exists: boolean;
  path: string;
}

let configBootstrap: Promise<ProjectConfig> | null = null;

export function loadProjectConfig(): Promise<ProjectConfig> {
  if (configBootstrap) return configBootstrap;
  configBootstrap = (async () => {
    let saved = await api<ProjectConfig>("/settings/local");
    if (!saved.exists) {
      const legacy = getLegacyBrowserConfig();
      if (legacy) {
        try {
          saved = await api<ProjectConfig>("/settings/local", legacy);
        } catch (error) {
          setRuntimeConfig(legacy);
          throw error;
        }
      }
    }
    setRuntimeConfig(saved.config);
    clearLegacyBrowserConfig();
    return saved;
  })().catch((error) => {
    configBootstrap = null;
    throw error;
  });
  return configBootstrap;
}

export async function saveProjectConfig(config: UserAPIConfig) {
  const saved = await api<ProjectConfig>("/settings/local", config);
  setRuntimeConfig(saved.config);
  clearLegacyBrowserConfig();
  configBootstrap = Promise.resolve(saved);
  return saved;
}

export async function clearProjectConfig() {
  const saved = await api<ProjectConfig>("/settings/local/clear", {});
  setRuntimeConfig({ ...emptyConfig });
  clearLegacyBrowserConfig();
  configBootstrap = Promise.resolve(saved);
  return saved;
}

export interface Country {
  code: string;
  name: string;
  english_name: string;
}
export interface Region {
  id: string;
  country_code: string;
  name: string;
  city_count: number;
}
export interface Destination {
  id: number;
  country_code: string;
  state_id: string;
  name: string;
  english_name: string;
  longitude: number;
  latitude: number;
  timezone: string | null;
  coordinate_system: "WGS84";
  country?: string;
  state?: string;
}

export async function waitForRun(
  id: string,
  onEvents: (events: RunEvent[]) => void,
): Promise<Run> {
  for (let attempt = 0; attempt < 360; attempt++) {
    const run = await api<Run>(`/runs/${id}`);
    onEvents(run.events);
    if (run.status === "failed")
      throw new Error(run.error || "规划失败，请重试");
    if (run.status === "completed") return run;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("任务仍在执行，可以稍后刷新恢复查看。");
}
