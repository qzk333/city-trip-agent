import { ShieldCheck } from "lucide-react";
import type { Itinerary } from "./api";

export default function ItineraryChecks({
  plan,
  statusLabel,
}: {
  plan: Itinerary;
  statusLabel: string;
}) {
  const review = plan.planning_decision?.quality_review;
  const outcome = plan.planning_decision?.review_outcome;
  const seen = new Set<string>();
  const checks = plan.checks.filter((c) => {
    if (seen.has(c.message)) return false;
    seen.add(c.message);
    return !(
      c.code === "night_preference_unverified" &&
      plan.checks.some((other) => other.code === "schedule_night_missing")
    );
  });
  const conflicts = checks.filter((c) => c.severity === "error");
  const preferences = checks.filter(
    (c) =>
      c.severity !== "error" &&
      ((c.code.startsWith("schedule_") &&
        c.code !== "schedule_review_unavailable") ||
        c.code === "recommendation_time_mismatch" ||
        c.code.startsWith("reviewer_") ||
        c.code.startsWith("weather_outdoor")),
  );
  const changes = checks.filter(
    (c) => c.severity === "info" && c.code.endsWith("adjusted"),
  );
  const repair = checks.filter((c) =>
    ["schedule_review_unavailable", "quality_review_unavailable"].includes(
      c.code,
    ),
  );
  const classified = new Set([
    ...conflicts,
    ...preferences,
    ...changes,
    ...repair,
  ]);
  const pending = checks.filter((c) => !classified.has(c));
  const list = (items: typeof checks) => (
    <ul>
      {items.map((c, i) => (
        <li
          key={`${c.code}-${i}`}
          className={c.severity === "error" ? "error-text" : ""}
        >
          <span className={`check-dot ${c.severity}`} />
          {c.message}
        </li>
      ))}
    </ul>
  );
  return (
    <section
      className="validation-card"
      id="itinerary-checks"
      aria-label="行程检查"
    >
      <div className="section-label">
        <ShieldCheck size={16} />
        <span>行程检查</span>
        <span className={conflicts.length ? "error-text" : "muted"}>
          {conflicts.length ? "草案仍有冲突，暂不能保存" : statusLabel}
        </span>
      </div>
      {outcome && outcome !== "not_requested" && (
        <p className="check-group" role="status" aria-label="审核后调整结果">
          {
            {
              applied: "已采纳调整后的时间表；剩余建议见逐日审核。",
              unchanged: "已复核，未取得更合适的时间表；当前保留原草案。",
              failed: "已尝试调整，但修改结果未通过校验；当前展示调整前草案。",
              budget_exhausted:
                "本轮修改预算不足，尚未完成调整；当前保留原草案。",
            }[outcome]
          }
        </p>
      )}
      {review && review.status !== "not_requested" && (
        <div
          className="check-group quality-review"
          aria-label="审核 Agent 报告"
        >
          <h3>
            独立体验审核 ·{" "}
            {review.status === "passed"
              ? "未发现需调整的体验问题"
              : review.status === "changes_requested"
                ? "仍有改进建议"
                : "暂未完成"}
          </h3>
          <p>{review.summary}</p>
          {!!review.days.length && (
            <details>
              <summary>查看逐日审核依据</summary>
              <ul>
                {review.days.map((day) => (
                  <li key={day.day}>
                    第 {day.day} 天 · 晚间
                    {
                      {
                        scheduled: "已有安排",
                        rest_justified: "合理休息",
                        not_applicable: "不适用",
                        missing: "待补充",
                      }[day.evening]
                    }
                    ：{day.explanation}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <small>
            体验审核属于模型判断；交通、预约及时间冲突由程序独立检查。
          </small>
        </div>
      )}
      {!!conflicts.length && (
        <div className="check-group" role="alert">
          <h3>待解决冲突（{conflicts.length}）</h3>
          {list(conflicts)}
        </div>
      )}
      {!!preferences.length && (
        <div className="check-group">
          <h3>安排偏好与体验（{preferences.length}）</h3>
          {list(preferences)}
        </div>
      )}
      {!!repair.length && (
        <div className="check-group">
          <h3>修复状态</h3>
          {list(repair)}
          {plan.planning_decision?.review_failure && (
            <small>
              复核结果：{plan.planning_decision.review_failure.message} ·{" "}
              {plan.planning_decision.review_failure.retryable
                ? "可在服务恢复后重新生成"
                : "需重新规划或检查返回格式"}
            </small>
          )}
          {!!plan.planning_decision?.review_failure?.details?.length && (
            <details>
              <summary>查看具体失败原因</summary>
              <ul>
                {plan.planning_decision.review_failure.details.map(
                  (issue, index) => (
                    <li key={`${issue.code}-${index}`}>{issue.message}</li>
                  ),
                )}
              </ul>
            </details>
          )}
        </div>
      )}
      {!!pending.length && (
        <div className="check-group">
          <h3>出行前待确认（{pending.length}）</h3>
          {list(pending)}
        </div>
      )}
      {!!changes.length && (
        <details className="check-group check-changes">
          <summary>查看程序调整记录（{changes.length}）</summary>
          {list(changes)}
        </details>
      )}
    </section>
  );
}
