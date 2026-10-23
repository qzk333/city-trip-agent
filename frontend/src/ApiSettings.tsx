import { useState } from "react";
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
  onSave,
  onClose,
}: {
  config: UserAPIConfig;
  onSave: (config: UserAPIConfig, clear?: boolean) => Promise<void>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState({ ...config });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const update = (key: keyof UserAPIConfig, value: string) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setStatus("");
  };
  async function test(service: "model" | "amap") {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      const result = await api<{ message: string }>(
        "/settings/test",
        { service },
        draft,
      );
      setStatus(result.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "连接失败");
    } finally {
      setBusy(false);
    }
  }
  async function save(clear = false) {
    setBusy(true);
    setError("");
    try {
      await onSave(clear ? { ...emptyConfig } : draft, clear);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
      setBusy(false);
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
          使用你自己的服务与额度。Key
          只保存在当前标签页，请求时交给服务端转发，不写入行程或数据库。
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset className="settings-section">
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
              disabled={
                busy ||
                draft.planning_mode !== "llm" ||
                !draft.llm_api_key ||
                !draft.llm_base_url ||
                !draft.llm_model
              }
              onClick={() => void test("model")}
            >
              {busy && <LoaderCircle size={13} className="spin" />}测试模型连接
            </button>
          </fieldset>
          <fieldset className="settings-section">
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
              用于中国大陆的地点、路线和天气查询。境外目的地可先使用演示模式。
            </p>
            <button
              type="button"
              className="outline-button"
              disabled={
                busy || draft.tool_provider !== "amap" || !draft.amap_api_key
              }
              onClick={() => void test("amap")}
            >
              测试高德连接
            </button>
            <details className="map-key-details">
              <summary>显示真实地图（可选）</summary>
              <p>
                浏览器地图需要单独的 JS API Key
                和安全密钥；未配置时保留路线示意。
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
          {status && (
            <div className="message success" role="status">
              {status}
            </div>
          )}
          <div className="settings-footer">
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => void save(true)}
            >
              清除本标签页配置
            </button>
            <button className="primary" disabled={busy} type="submit">
              {busy && <LoaderCircle size={14} className="spin" />}保存 API 设置
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
