import type {
  Place,
  PlaceRecommendation,
  Itinerary,
  Activity,
  VenueAssessment,
} from "./api";
import { nightMismatch, venuePresentation } from "./planningPresentation";

export default function RecommendationBasis({
  place,
  recommendation,
  sources = [],
  compact = false,
  activity,
  assessment,
  review,
}: {
  place?: Place;
  recommendation?: PlaceRecommendation;
  sources?: NonNullable<Itinerary["guides"]>["sources"];
  compact?: boolean;
  activity?: Activity;
  assessment?: VenueAssessment;
  review?: NonNullable<Itinerary["planning_decision"]>["quality_review"];
}) {
  if (!place) return null;
  const evidence = place.traits?.evidence || [];
  const retrieval = evidence.filter((e) => e.field === "retrieval");
  const identity = evidence.find((e) => e.field === "identity");
  const guideReferences = evidence.filter((e) => e.field === "guide_reference");
  const mismatch = nightMismatch(activity, recommendation, review);
  const venue = venuePresentation(place, assessment);
  const content = (
    <>
      {recommendation && (
        <>
          {mismatch ? (
            <>
              <p className="basis-timing-warning">
                本次安排为 {activity?.start}—{activity?.end}
                ，尚未落实晚间观景；原推荐理由与实际时段不一致。
              </p>
              <details>
                <summary>查看模型原始推荐理由（时段未落实）</summary>
                <p>{recommendation.reason}</p>
              </details>
            </>
          ) : (
            <p>{recommendation.reason}</p>
          )}
          <small>
            {recommendation.basis === "model"
              ? "模型判断，引用资料仍需核实"
              : "程序匹配说明"}
          </small>
          {recommendation.tradeoff && (
            <p className="basis-limit">取舍与限制：{recommendation.tradeoff}</p>
          )}
        </>
      )}
      {assessment && venue.inferred && (
        <p className="venue-assessment">
          <b>环境判断：</b>
          {venue.label}，置信程度
          {{ high: "较高", medium: "中等", low: "较低" }[assessment.confidence]}
          。{assessment.reason}
          {venue.inferred && " 属于模型推断，未实地核验。"}
        </p>
      )}
      {!recommendation && !compact && (
        <>
          <p>旧版本未记录具体推荐理由，重新生成预览后可查看。</p>
          <small>以下是目录与程序筛选信息，不代表模型选择该地点的理由。</small>
          {place.candidate_match && place.candidate_match.relevance > 0 && (
            <p>程序匹配说明：{place.candidate_match.reasons.join("；")}</p>
          )}
        </>
      )}
      {retrieval.length ? (
        <p>
          <b>检索线索：</b>
          {retrieval.map((e) => `“${e.value}”`).join("、")}
          。表示地图查询命中，不是人气或必去排名。
        </p>
      ) : (
        <p>此版本没有保存具体检索词；地点信息来自已有目录。</p>
      )}
      {guideReferences.map((e, i) => (
        <p key={`guide-reference-${i}`}>
          <b>攻略补充线索：</b>
          {e.value} · 已匹配地图地点身份。
          {e.source_url && (
            <a href={e.source_url} target="_blank" rel="noreferrer">
              查看引入此候选的攻略 ↗
            </a>
          )}
        </p>
      ))}
      {identity?.excerpt && (
        <p>
          <b>地图分类：</b>
          {identity.excerpt}
        </p>
      )}
      {identity?.observed_at && (
        <p>
          <b>查询时间：</b>
          {new Date(identity.observed_at).toLocaleString("zh-CN")}
        </p>
      )}
      {recommendation?.source_ids.map((id) => {
        const source = sources.find((s) => s.id === id);
        return source ? (
          <a key={id} href={source.url} target="_blank" rel="noreferrer">
            参考资料：{source.title} ↗
          </a>
        ) : null;
      })}
      {place.source_url && (
        <a href={place.source_url} target="_blank" rel="noreferrer">
          地图地点来源 ↗
        </a>
      )}
      <p className="basis-limit">
        {place.fixture
          ? "演示数据，不是真实出行依据。"
          : "地图核对地点身份与位置；不证明营业、预约、适合人群或景区内部步行条件。"}
      </p>
    </>
  );
  return compact ? (
    <details className="recommendation-basis compact">
      <summary>查看检索依据与来源</summary>
      {content}
    </details>
  ) : (
    <section
      className="recommendation-basis"
      aria-label={`${place.name}推荐依据`}
    >
      <h4>{recommendation ? "为什么推荐这里" : "推荐理由未记录"}</h4>
      {content}
    </section>
  );
}
