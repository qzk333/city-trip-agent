export interface Location {
  name: string;
  longitude: number;
  latitude: number;
  coordinate_system: "GCJ-02";
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
}
export interface Requirements {
  city: "杭州";
  start_date: string;
  days: number;
  day_start: string;
  day_end: string;
  hotel: Location;
  transport: "transit" | "walking";
  pace: "relaxed" | "balanced";
  interests: Place["category"][];
  must_visit: string[];
  appointments: { place_id: string; day: number; start: string; end: string }[];
  note: string;
}
export interface Activity {
  id: string;
  place_id: string;
  day: number;
  start: string;
  end: string;
  locked: boolean;
  reason: string;
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
}
export interface DayPlan {
  day: number;
  date: string;
  activities: Activity[];
  legs: RouteLeg[];
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
  day_start_offsets: Record<string, number>;
}
export interface Proposal {
  id: string;
  base_version: number;
  itinerary: Itinerary;
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

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
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

export async function waitForRun(
  id: string,
  onEvents: (events: RunEvent[]) => void,
): Promise<Run> {
  for (let attempt = 0; attempt < 240; attempt++) {
    const run = await api<Run>(`/runs/${id}`);
    onEvents(run.events);
    if (run.status === "failed")
      throw new Error(run.error || "规划失败，请重试");
    if (run.status === "completed") return run;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("任务仍在执行，可以稍后刷新恢复查看。");
}
