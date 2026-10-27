import type {
  Activity,
  Place,
  PlaceRecommendation,
  VenueAssessment,
  Itinerary,
} from "./api";

export function nightMismatch(
  activity?: Activity,
  _recommendation?: PlaceRecommendation,
  review?: NonNullable<Itinerary["planning_decision"]>["quality_review"],
) {
  return (
    !!activity &&
    !!review &&
    review.status !== "unavailable" &&
    review.issues.some(
      (issue) =>
        issue.category === "rationale" &&
        issue.day === activity.day &&
        issue.place_ids.includes(activity.place_id) &&
        /夜|晚间|傍晚/.test(issue.evidence),
    )
  );
}
export function venuePresentation(place?: Place, assessment?: VenueAssessment) {
  const observed = place?.traits?.indoor_outdoor;
  const value =
    observed && observed !== "unknown"
      ? observed
      : place?.fixture
        ? place.indoor
          ? "indoor"
          : "outdoor"
        : assessment?.indoor_outdoor || "unknown";
  const labels = {
    indoor: "室内为主",
    outdoor: "室外为主",
    mixed: "室内外混合",
    unknown: "室内外尚不明确",
  };
  const inferred =
    (!observed || observed === "unknown") && !place?.fixture && !!assessment;
  return {
    label: labels[value] + (inferred ? " · 模型判断" : ""),
    inferred,
    detail: inferred ? assessment?.reason || "" : "",
  };
}
