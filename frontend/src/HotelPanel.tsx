import { useState } from "react";
import { BedDouble, Check, MapPin } from "lucide-react";
import type { HotelChoice, HotelResearch, Location, Place } from "./api";

export default function HotelPanel({
  lodging,
  places,
  disabled,
  needsSave,
  onChoose,
  boundaryLocations = {},
}: {
  lodging: HotelResearch;
  places: Place[];
  disabled: boolean;
  needsSave: boolean;
  onChoose: (choice: HotelChoice) => void;
  boundaryLocations?: Record<string, Location | undefined>;
}) {
  const [custom, setCustom] = useState<Record<number, Location>>({});
  const [activeNight, setActiveNight] = useState(1);
  if (lodging.status === "not_requested") return null;
  const name = (id: string) =>
    places.find((p) => p.id === id)?.name ||
    boundaryLocations[id]?.name ||
    "尚未安排";
  return (
    <section className="hotel-panel" aria-label="住宿候选">
      <div className="section-label">
        <BedDouble size={17} />
        <span>路线之后，选一处好休息的地方</span>
      </div>
      <p className="hotel-intro">{lodging.note}</p>
      {needsSave && lodging.nights.length > 0 && (
        <p className="hotel-save-hint">
          先保存路线草案，再选择酒店。每次选择都会生成调整预览，确认后保存。
        </p>
      )}
      {lodging.nights.length > 1 && (
        <div className="hotel-night-tabs" role="tablist" aria-label="住宿日期">
          {lodging.nights.map((stay) => (
            <button
              key={stay.night}
              type="button"
              role="tab"
              aria-selected={activeNight === stay.night}
              onClick={() => setActiveNight(stay.night)}
            >
              第 {stay.night} 晚{stay.selected_id ? " · 已选" : " · 待选"}
            </button>
          ))}
        </div>
      )}
      {lodging.nights
        .filter(
          (stay) =>
            stay.night ===
            (lodging.nights.some((n) => n.night === activeNight)
              ? activeNight
              : 1),
        )
        .map((stay) => {
          const anchor =
            places.find((p) => p.id === stay.last_place_id) ||
            boundaryLocations[stay.last_place_id];
          const location = custom[stay.night] || {
            name: "",
            longitude: anchor?.longitude || 0,
            latitude: anchor?.latitude || 0,
            coordinate_system: anchor?.coordinate_system || "GCJ-02",
          };
          const update = (values: Partial<Location>) =>
            setCustom({
              ...custom,
              [stay.night]: { ...location, ...values },
            });
          return (
            <div
              key={stay.night}
              className="hotel-night"
              data-night={stay.night}
            >
              <div className="hotel-night-heading">
                <h3>第 {stay.night} 晚住宿</h3>
                <span>
                  {stay.checkin.slice(5)} → {stay.checkout.slice(5)}
                </span>
              </div>
              <p className="hotel-anchors">
                当晚末站：{name(stay.last_place_id)} · 次日首站：
                {name(stay.next_place_id)}
              </p>
              {stay.note && <p className="hotel-save-hint">{stay.note}</p>}
              <div className="hotel-options">
                {stay.candidates.map((hotel) => {
                  const selected = stay.selected_id === hotel.id;
                  const shared = lodging.nights.some(
                    (other) =>
                      other.night !== stay.night &&
                      other.candidates.some((h) => h.id === hotel.id),
                  );
                  return (
                    <article
                      key={hotel.id}
                      className={`hotel-option${selected ? " chosen" : ""}`}
                    >
                      <span className="hotel-source">
                        {hotel.provider === "demo"
                          ? "演示位置 · 非真实酒店"
                          : hotel.provider === "user"
                            ? "你填写的酒店"
                            : "高德酒店地点"}
                      </span>
                      <h4>{hotel.location.name}</h4>
                      <p>
                        <MapPin size={12} />
                        {hotel.address || "位置以地图坐标为准"}
                      </p>
                      <div className="hotel-commute">
                        <span>
                          当晚到店{" "}
                          <strong>约 {hotel.evening_minutes} 分钟</strong>
                        </span>
                        <span>
                          次日出发{" "}
                          <strong>约 {hotel.morning_minutes} 分钟</strong>
                        </span>
                      </div>
                      <p className="hotel-estimate">
                        直线距离分别约{" "}
                        {(hotel.evening_distance_meters / 1000).toFixed(1)} /{" "}
                        {(hotel.morning_distance_meters / 1000).toFixed(1)}{" "}
                        km；以上为距离估时，未查询导航。
                      </p>
                      {shared && (
                        <p className="hotel-shared">
                          也出现在其他晚候选中，可比较连续入住
                        </p>
                      )}
                      <p className="hotel-price">
                        酒店位置参考 · 房型和价格请自行核实
                      </p>
                      {hotel.observed_at && (
                        <p className="hotel-estimate">
                          {hotel.provider === "user" ? "填写" : "查询"}时间：
                          {new Date(hotel.observed_at).toLocaleString("zh-CN")}
                        </p>
                      )}
                      <div className="hotel-option-actions">
                        {hotel.source_url && (
                          <a
                            href={hotel.source_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            查看地图位置 ↗
                          </a>
                        )}
                        <button
                          className="outline-button"
                          type="button"
                          aria-label={`${selected ? "已选择" : "选择"}第${stay.night}晚${hotel.location.name}`}
                          disabled={disabled || selected}
                          aria-pressed={selected}
                          onClick={() =>
                            onChoose({
                              night: stay.night,
                              candidate_id: hotel.id,
                            })
                          }
                        >
                          {selected && <Check size={14} />}
                          {selected ? "已选择" : "选择这家"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
              {stay.last_place_id && stay.next_place_id && (
                <details className="hotel-custom">
                  <summary>已经订好酒店？填写酒店名称和位置</summary>
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      onChoose({
                        night: stay.night,
                        custom_location: location,
                      });
                    }}
                  >
                    <label>
                      酒店名称
                      <input
                        aria-label={`第${stay.night}晚已订酒店名称`}
                        required
                        maxLength={100}
                        value={location.name}
                        onChange={(e) => update({ name: e.target.value })}
                      />
                    </label>
                    <div className="field-grid">
                      <label>
                        经度
                        <input
                          aria-label={`第${stay.night}晚酒店经度`}
                          type="number"
                          required
                          min={-180}
                          max={180}
                          step="any"
                          value={location.longitude}
                          onChange={(e) =>
                            update({ longitude: Number(e.target.value) })
                          }
                        />
                      </label>
                      <label>
                        纬度
                        <input
                          aria-label={`第${stay.night}晚酒店纬度`}
                          type="number"
                          required
                          min={-90}
                          max={90}
                          step="any"
                          value={location.latitude}
                          onChange={(e) =>
                            update({ latitude: Number(e.target.value) })
                          }
                        />
                      </label>
                    </div>
                    <label>
                      坐标系
                      <select
                        aria-label={`第${stay.night}晚酒店坐标系`}
                        value={location.coordinate_system}
                        onChange={(e) =>
                          update({
                            coordinate_system: e.target
                              .value as Location["coordinate_system"],
                          })
                        }
                      >
                        <option value="GCJ-02">高德 GCJ-02</option>
                        <option value="WGS84">GPS WGS84</option>
                      </select>
                    </label>
                    <p className="hotel-estimate">
                      名称用于展示，请核对酒店实际坐标；不会根据名称自动定位。
                    </p>
                    <button
                      className="outline-button"
                      disabled={disabled}
                      type="submit"
                    >
                      使用第 {stay.night} 晚已订酒店
                    </button>
                  </form>
                </details>
              )}
            </div>
          );
        })}
      {lodging.analysis_status === "unavailable" && (
        <p className="hotel-estimate">
          酒店模型分析暂不可用，候选按位置比较排列。
        </p>
      )}
    </section>
  );
}
