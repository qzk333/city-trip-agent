import type { Requirements, TravelProfile } from "./api";

export const emptyProfile: TravelProfile = {
  companions: "unspecified",
  themes: [],
  walking: "normal",
  excluded_keywords: [],
};
const themes: Record<string, string> = {
  history: "历史文化",
  architecture: "建筑街区",
  night: "夜景",
  photo: "拍照",
  nature: "自然",
  food: "美食",
  coffee: "咖啡探店",
  culture: "人文展览",
  city: "城市漫步",
};

export function candidateRequestKey(form: Requirements) {
  return JSON.stringify([
    form.city_id,
    form.start_date,
    form.days,
    form.pace,
    {
      ...(form.profile || emptyProfile),
      themes: [...(form.profile || emptyProfile).themes].sort(),
      excluded_keywords: [
        ...(form.profile || emptyProfile).excluded_keywords,
      ].sort(),
    },
    form.note,
  ]);
}

export default function TravelPreferences({
  form,
  onChange,
  disabled,
}: {
  form: Requirements;
  onChange: (value: Requirements) => void;
  disabled: boolean;
}) {
  const profile = form.profile || emptyProfile;
  function update(patch: Partial<TravelProfile>) {
    onChange({ ...form, profile: { ...profile, ...patch } });
  }
  return (
    <fieldset disabled={disabled} className="travel-preferences">
      <legend>这次想怎么玩</legend>
      <div className="field-grid">
        <div className="field">
          <label htmlFor="companions">和谁出行</label>
          <select
            id="companions"
            value={profile.companions}
            onChange={(e) =>
              update({
                companions: e.target.value as TravelProfile["companions"],
              })
            }
          >
            <option value="unspecified">暂不填写</option>
            <option value="solo">独自旅行</option>
            <option value="friends">和朋友</option>
            <option value="parents">和父母</option>
            <option value="children">带孩子</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="walking-preference">步行偏好</label>
          <select
            id="walking-preference"
            value={profile.walking}
            onChange={(e) =>
              update({ walking: e.target.value as TravelProfile["walking"] })
            }
          >
            <option value="normal">正常安排</option>
            <option value="less">少走路，集中安排</option>
          </select>
        </div>
      </div>
      <div className="interest-options">
        {Object.entries(themes).map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={profile.themes.includes(
              key as TravelProfile["themes"][number],
            )}
            className={
              profile.themes.includes(key as TravelProfile["themes"][number])
                ? "active"
                : ""
            }
            onClick={() =>
              update({
                themes: profile.themes.includes(
                  key as TravelProfile["themes"][number],
                )
                  ? profile.themes.filter((t) => t !== key)
                  : [...profile.themes, key as TravelProfile["themes"][number]],
              })
            }
          >
            {label}
          </button>
        ))}
      </div>
      <div className="field">
        <label htmlFor="note">偏好补充</label>
        <textarea
          id="note"
          rows={2}
          maxLength={1000}
          value={form.note}
          placeholder="例如：带父母、喜欢历史、少走路，不去商场。"
          onChange={(e) => onChange({ ...form, note: e.target.value })}
        />
      </div>
      <p className="field-hint">
        偏好会用于查找与排序。适合人群、景区内部步行和营业信息需核实；复杂要求仍可能需要补充说明。
      </p>
    </fieldset>
  );
}
