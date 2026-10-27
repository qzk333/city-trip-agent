"""Demand-driven, budgeted discovery through the existing per-key map gateway."""

from app.candidate_ranking import rank_candidates
from app.catalog import demo_catalog
from app.schemas import AttributeEvidence, Location
from app.tools import ToolUnavailable
from app.travel_profile import discovery_fingerprint, normalize_profile

QUERY_THEMES = {
    "history": [("历史博物馆", "culture"), ("历史街区", "city")],
    "architecture": [("古建筑", "culture"), ("建筑街区", "city")],
    "night": [("夜景观景", "city"), ("滨水夜游", "nature")],
    "photo": [("摄影景点", "nature"), ("建筑街区", "city")],
    "nature": [("公园", "nature"), ("湖泊", "nature")],
    "food": [("美食街", "city")],
    "coffee": [("特色咖啡", "coffee")],
    "culture": [("博物馆", "culture"), ("艺术展览", "culture")],
    "city": [("历史街区", "city"), ("步行街", "city")],
}


def query_plan(profile):
    # Keep a city overview channel even when the user has narrow interests.
    # It proposes candidates, not compulsory attractions or a popularity ranking.
    queries = [("景点", "nature")]
    # Fine themes replace redundant broad searches; each active theme gets a first query.
    active = [
        t
        for t in QUERY_THEMES
        if t in profile.themes
        if not (t == "culture" and {"history", "architecture"} & set(profile.themes))
        and not (t == "city" and {"architecture", "night"} & set(profile.themes))
    ]
    queries += [QUERY_THEMES[t][0] for t in active]
    queries += [q for t in active for q in QUERY_THEMES[t][1:]]
    if not active:
        # An unspecified theme still needs a varied city catalog, including places
        # that can support an evening; this is retrieval, not verified night opening.
        queries += [("特色街区", "city"), ("步行街", "city"), ("博物馆", "culture")]
    return list(dict.fromkeys(queries))[:6]


def merge_query(pool, found, query, city):
    for index, (pid, place) in enumerate(found.items()):
        evidence = pool[pid].traits.evidence if pid in pool else place.traits.evidence
        evidence = [e for e in evidence if e.field != "retrieval" or e.value != query]
        evidence = [
            *evidence,
            AttributeEvidence(
                field="retrieval",
                value=query,
                kind="observed",
                source_url=place.source_url,
                observed_at=next((e.observed_at for e in place.traits.evidence if e.field == "identity"), ""),
                excerpt=f"{city}内关键词查询的第 {index + 1} 条有效返回；仅表示检索位置，不是热度排名。",
            ),
        ]
        traits = place.traits.model_copy(update={"evidence": evidence[-12:]})
        pool[pid] = place.model_copy(update={"traits": traits})


def recommend_places(body, destination, tools, previous=None):
    profile = normalize_profile(body.profile, body.note)
    protected = {pid: previous[pid] for pid in body.keep_ids if previous and pid in previous}
    if set(body.keep_ids) - set(protected):
        raise ValueError("要保留的地点不属于当前城市的已保存目录，请重新选择。")
    queries = query_plan(profile)
    records = []
    if tools.settings.tool_provider == "demo":
        pool = demo_catalog(body.city_id)
        center = Location(
            name=f"演示参考位置 · {destination['name']}",
            longitude=destination["longitude"],
            latitude=destination["latitude"],
            coordinate_system=destination["coordinate_system"],
        )
    else:
        geo = tools._get("/v3/geocode/geo", {"address": destination["state"] + destination["name"]})
        geocodes = geo.get("geocodes", [])
        if not geocodes or not geocodes[0].get("adcode"):
            raise ToolUnavailable("未识别到当前城市的区域信息，请核对目的地。")
        tools.adcode = geocodes[0]["adcode"]
        lon, lat = map(float, geocodes[0]["location"].split(","))
        center = Location(name=f"参考位置 · {destination['name']}", longitude=lon, latitude=lat)
        pool = {}
        # Six primary searches plus at most two targeted fallbacks; failed attempts count too.
        for query, category in queries:
            try:
                found = tools.search_candidates(query, category, limit=6)
                merge_query(pool, found, query, destination["name"])
                records.append(
                    {
                        "query": query,
                        "category": category,
                        "status": "ok" if found else "no_result",
                        "count": len(found),
                    }
                )
            except ToolUnavailable as exc:
                if exc.fatal:
                    raise
                records.append({"query": query, "category": category, "status": "unavailable", "count": 0})
        for query, category in [("博物馆", "culture"), ("公园", "nature")]:
            if len(pool) >= body.days * 4 or (query, category) in queries or len(records) >= 8:
                continue
            try:
                found = tools.search_candidates(query, category, limit=5)
                merge_query(pool, found, query, destination["name"])
                records.append(
                    {
                        "query": query,
                        "category": category,
                        "status": "ok" if found else "no_result",
                        "count": len(found),
                    }
                )
            except ToolUnavailable as exc:
                if exc.fatal:
                    raise
                records.append({"query": query, "category": category, "status": "unavailable", "count": 0})
        if not pool and not protected:
            raise ToolUnavailable("未取得可用候选地点；请调整偏好或检查高德权限后重试。")
    pool.update(protected)  # Existing decisions retain their original evidence.
    ranked = rank_candidates(pool, profile, protected=protected)
    if not ranked:
        raise ToolUnavailable("当前排除条件下没有可用候选，请调整偏好或明确希望保留的地点。")
    fingerprint = discovery_fingerprint(
        body.city_id, body.start_date, body.days, body.pace, body.profile, body.note
    )
    partial = any(r["status"] != "ok" for r in records)
    return {
        "places": {pid: p.model_dump(mode="json") for pid, p in ranked.items()},
        "center": center.model_dump(),
        "profile": profile.model_dump(),
        "metadata": {
            "schema_version": 1,
            "preference_schema_version": 2,
            "strategy": "demand-v1",
            "fingerprint": fingerprint,
            "queries": records,
            "profile": profile.model_dump(),
            "partial": partial,
            "trace": tools.trace,
        },
        "note": ("部分检索未取得结果；已保留可用候选。" if partial else "已按旅行需求整理候选。")
        + "停留时长为估算；室内外、内部步行、营业及适合人群仍需核实。",
    }
