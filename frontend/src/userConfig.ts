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

export function getBrowserConfig(): UserAPIConfig {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
    const config = { ...emptyConfig };
    for (const field of Object.keys(config) as (keyof UserAPIConfig)[]) {
      if (typeof parsed[field] === "string")
        Object.assign(config, { [field]: parsed[field] });
    }
    if (!["demo", "amap"].includes(config.tool_provider))
      config.tool_provider = "demo";
    if (!["demo", "llm"].includes(config.planning_mode))
      config.planning_mode = "demo";
    return config;
  } catch {
    return { ...emptyConfig };
  }
}

export function saveBrowserConfig(config: UserAPIConfig) {
  sessionStorage.setItem(storageKey, JSON.stringify(config));
}

export function clearBrowserConfig() {
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
