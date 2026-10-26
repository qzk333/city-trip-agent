import { useEffect, useRef, useState } from "react";
import {
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  ShieldCheck,
  X,
} from "lucide-react";
import { api } from "./api";
import { emptyConfig } from "./userConfig";
import type { UserAPIConfig } from "./userConfig";

type ConnectionFeedback = { error: boolean; message: string };

function ConnectionResult({
  id,
  result,
}: {
  id: string;
  result?: ConnectionFeedback;
}) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (result?.message) element.current?.scrollIntoView({ block: "nearest" });
  }, [result]);
  return (
    <div
      ref={element}
      id={id}
      className={`connection-result${result?.error ? " error" : ""}`}
      role={result?.error ? "alert" : "status"}
      aria-live="polite"
    >
      {result?.message}
    </div>
  );
}

function SecretField({
  id,
  label,
  value,
  onChange,
  required = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="secret-field">
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          required={required}
          autoComplete="off"
          spellCheck={false}
          maxLength={2048}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          aria-label={`${visible ? "隐藏" : "显示"}${label}`}
          onClick={() => setVisible(!visible)}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </div>
  );
}

export default function ApiSettings({
  config,
  configPath,
  onSave,
  onClose,
}: {
  config: UserAPIConfig;
  configPath: string;
  onSave: (config: UserAPIConfig, clear?: boolean) => Promise<void>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState({ ...config });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<"model" | "amap" | null>(null);
  const busy = saving || testing !== null;
  const [error, setError] = useState("");
  const [testResults, setTestResults] = useState<
    Partial<Record<"model" | "amap", ConnectionFeedback>>
  >({});
  const update = (key: keyof UserAPIConfig, value: string) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setTestResults({});
    setError("");
  };
  async function test(service: "model" | "amap") {
    const validation =
      service === "model"
        ? draft.planning_mode !== "llm"
          ? "请先将规划模式切换为「使用我的模型 API」。"
          : !draft.llm_api_key.trim() ||
              !draft.llm_base_url.trim() ||
              !draft.llm_model.trim()
            ? "请填写模型服务地址、模型名称和模型 API Key 后再测试。"
            : ""
        : draft.tool_provider !== "amap"
          ? "请先将地点与交通数据切换为「使用我的高德 Web 服务」。"
          : !draft.amap_api_key.trim()
            ? "请填写高德 Web 服务 Key 后再测试。"
            : "";
    setError("");
    if (validation) {
      setTestResults((current) => ({
        ...current,
        [service]: { error: true, message: validation },
      }));
      return;
    }
    setTesting(service);
    setTestResults((current) => ({
      ...current,
      [service]: { error: false, message: "正在连接服务，最多等待 20 秒…" },
    }));
    try {
      const result = await api<{ message: string }>(
        "/settings/test",
        { service },
        {
          ...draft,
          llm_base_url: draft.llm_base_url.trim(),
          ...(service === "model"
            ? { tool_provider: "demo" }
            : { planning_mode: "demo" }),
        },
        { signal: AbortSignal.timeout(20_000) },
      );
      setTestResults((current) => ({
        ...current,
        [service]: { error: false, message: result.message },
      }));
    } catch (e) {
      const message =
        e instanceof Error &&
        (e.name === "TimeoutError" || e.name === "AbortError")
          ? "连接测试超时（20 秒），请检查网络或服务地址后重试。"
          : e instanceof TypeError
            ? "无法连接应用后端，请检查本地服务是否仍在运行。"
            : e instanceof Error
              ? e.message
              : "连接失败，请重试。";
      setTestResults((current) => ({
        ...current,
        [service]: { error: true, message },
      }));
    } finally {
      setTesting(null);
    }
  }
  async function save(clear = false) {
    setSaving(true);
    setError("");
    try {
      await onSave(clear ? { ...emptyConfig } : draft, clear);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
      setSaving(false);
    }
  }
  return (
    <div className="modal-backdrop" onClick={() => !busy && onClose()}>
      <section
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-title">
          <div>
            <span className="eyebrow">BRING YOUR OWN KEY</span>
            <h2 id="settings-title">
              <KeyRound size={19} />
              我的 API 设置
            </h2>
          </div>
          <button aria-label="关闭 API 设置" disabled={busy} onClick={onClose}>
            <X size={19} />
          </button>
        </div>
        <p className="settings-intro">
          <ShieldCheck size={17} />
          配置保存在项目本地文件，下次启动自动读取；不再保存到浏览器缓存。配置文件已被
          Git 忽略，不写入行程数据库。
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset className="settings-section" disabled={busy}>
            <legend>模型服务</legend>
            <div className="field">
              <label htmlFor="planning-mode">规划模式</label>
              <select
                id="planning-mode"
                value={draft.planning_mode}
                onChange={(e) => update("planning_mode", e.target.value)}
              >
                <option value="demo">规则演示 · 不调用模型</option>
                <option value="llm">使用我的模型 API</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="model-url">模型服务地址</label>
              <input
                id="model-url"
                type="url"
                required={draft.planning_mode === "llm"}
                placeholder="https://你的服务地址/v1"
                autoComplete="off"
                value={draft.llm_base_url}
                onChange={(e) => update("llm_base_url", e.target.value)}
              />
              <small>
                填写公开 HTTPS 地址前缀，应用会追加 /chat/completions。
              </small>
            </div>
            <div className="field-grid">
              <div className="field">
                <label htmlFor="model-name">模型名称</label>
                <input
                  id="model-name"
                  required={draft.planning_mode === "llm"}
                  maxLength={150}
                  value={draft.llm_model}
                  placeholder="服务提供方的模型 ID"
                  onChange={(e) => update("llm_model", e.target.value)}
                />
              </div>
              <SecretField
                id="model-key"
                label="模型 API Key"
                value={draft.llm_api_key}
                required={draft.planning_mode === "llm"}
                onChange={(v) => update("llm_api_key", v)}
              />
            </div>
            <button
              type="button"
              className="outline-button"
              disabled={busy}
              aria-describedby="model-test-result"
              onClick={() => void test("model")}
            >
              {testing === "model" && (
                <LoaderCircle size={13} className="spin" />
              )}
              {testing === "model" ? "正在测试模型…" : "测试模型连接"}
            </button>
            <ConnectionResult
              id="model-test-result"
              result={testResults.model}
            />
          </fieldset>
          <fieldset className="settings-section" disabled={busy}>
            <legend>高德地图</legend>
            <div className="field">
              <label htmlFor="map-provider">地点与交通数据</label>
              <select
                id="map-provider"
                value={draft.tool_provider}
                onChange={(e) => update("tool_provider", e.target.value)}
              >
                <option value="demo">演示地点与路线估算</option>
                <option value="amap">使用我的高德 Web 服务</option>
              </select>
            </div>
            <SecretField
              id="amap-rest-key"
              label="高德 Web 服务 Key"
              value={draft.amap_api_key}
              required={draft.tool_provider === "amap"}
              onChange={(v) => update("amap_api_key", v)}
            />
            <p className="settings-help">
              在高德控制台选择「Web服务」，申请一个
              Key，用于地点搜索、地理编码、路线、天气、坐标转换和行政区域查询。无需分别申请六个
              Key。境外目的地可先使用演示模式。{" "}
              <a
                href="https://lbs.amap.com/api/webservice/gettingstarted"
                target="_blank"
                rel="noreferrer"
              >
                查看申请说明
              </a>
            </p>
            <button
              type="button"
              className="outline-button"
              disabled={busy}
              aria-describedby="amap-test-result"
              onClick={() => void test("amap")}
            >
              {testing === "amap" && (
                <LoaderCircle size={13} className="spin" />
              )}
              {testing === "amap" ? "正在测试高德…" : "测试高德连接"}
            </button>
            <ConnectionResult id="amap-test-result" result={testResults.amap} />
            <details className="map-key-details">
              <summary>显示真实地图（可选）</summary>
              <p>
                如需交互地图，再添加一个「Web端(JS
                API)」Key，并填写配套安全密钥（securityJsCode）。未配置时保留路线示意。{" "}
                <a
                  href="https://lbs.amap.com/api/javascript-api-v2/prerequisites"
                  target="_blank"
                  rel="noreferrer"
                >
                  查看申请说明
                </a>
              </p>
              <SecretField
                id="amap-js-key"
                label="高德 JS API Key"
                value={draft.amap_js_key}
                onChange={(v) => update("amap_js_key", v)}
              />
              <SecretField
                id="amap-security-code"
                label="高德 JS 安全密钥"
                value={draft.amap_security_code}
                onChange={(v) => update("amap_security_code", v)}
              />
            </details>
          </fieldset>
          {error && (
            <div className="message error" role="alert">
              {error}
            </div>
          )}
          <p className="settings-help">
            配置文件：<code className="config-file-path">{configPath}</code>
            可在本机查看和修改，Key 以明文保存在该文件中。
          </p>
          <div className="settings-footer">
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => void save(true)}
            >
              清除已保存配置
            </button>
            <button className="primary" disabled={busy} type="submit">
              {saving && <LoaderCircle size={14} className="spin" />}保存 API
              设置
            </button>
          </div>
          <p className="settings-help">
            连接测试会使用你的 API 额度。修改浏览器地图 Key 后会刷新页面。
          </p>
        </form>
      </section>
    </div>
  );
}
