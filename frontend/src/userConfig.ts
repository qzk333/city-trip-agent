export interface UserAPIConfig {
  tool_provider: "demo" | "amap";
  planning_mode: "demo" | "llm";
  amap_api_key: string;
  llm_base_url: string;
  llm_api_key: string;
  llm_model: string;
  amap_js_key: string;
  amap_security_code: string;
}

export const emptyConfig: UserAPIConfig = {
  tool_provider: "demo",
  planning_mode: "demo",
  amap_api_key: "",
  llm_base_url: "",
  llm_api_key: "",
  llm_model: "",
  amap_js_key: "",
  amap_security_code: "",
};
const storageKey = "city-trip:user-api-config:v1";
let runtimeConfig: UserAPIConfig = { ...emptyConfig };

export function getBrowserConfig(): UserAPIConfig {
  return { ...runtimeConfig };
}

export function setRuntimeConfig(config: UserAPIConfig) {
  runtimeConfig = { ...config };
}

// Read the previous browser format once for migration; never save new keys here.
export function getLegacyBrowserConfig(): UserAPIConfig | null {
  try {
    const parsed = JSON.parse(
      localStorage.getItem(storageKey) ??
        sessionStorage.getItem(storageKey) ??
        "{}",
    );
    const config = { ...emptyConfig };
    for (const field of Object.keys(config) as (keyof UserAPIConfig)[]) {
      if (typeof parsed[field] === "string")
        Object.assign(config, { [field]: parsed[field] });
    }
    if (!["demo", "amap"].includes(config.tool_provider))
      config.tool_provider = "demo";
    if (!["demo", "llm"].includes(config.planning_mode))
      config.planning_mode = "demo";
    return Object.entries(config).some(
      ([key, value]) =>
        !["tool_provider", "planning_mode"].includes(key) && value,
    )
      ? config
      : null;
  } catch {
    return null;
  }
}

export function clearLegacyBrowserConfig() {
  localStorage.removeItem(storageKey);
  sessionStorage.removeItem(storageKey);
}

export function requestConfigHeader(config: UserAPIConfig): string {
  const {
    amap_js_key: _js,
    amap_security_code: _security,
    ...serverConfig
  } = config;
  return encodeURIComponent(JSON.stringify(serverConfig));
}
