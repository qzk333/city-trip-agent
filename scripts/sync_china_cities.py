"""Build a Chinese prefecture-level destination list with stable existing city IDs.

Build-only dependency: pypinyin==0.55.0 (the app does not import it).
Names/levels: uiwjs/province-city-china, MIT, pinned below.
Reference coordinates/IDs: the existing ODbL geography snapshot.
"""

import gzip
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import httpx
from pypinyin import lazy_pinyin

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data/geography"
REVISION = "a708f0df0ebdb68f09f5fcf6345367f7d5e9a7dc"
SOURCE = f"https://raw.githubusercontent.com/uiwjs/province-city-china/{REVISION}"
# Transliterations differ between the two sources, especially ethnic place names.
MATCH_NAMES = {
    "140400": "Changzhi",
    "141100": "Lüliang",
    "150100": "Hohhot",
    "150600": "Ordos",
    "150700": "Hulunbuir",
    "150800": "Bayan Nur",
    "150900": "Ulanqab",
    "152200": "Hinggan",
    "152500": "Xilingol",
    "152900": "Alxa",
    "211300": "Chaoyang",
    "230100": "Harbin",
    "230200": "Qiqihar",
    "232700": "Daxing'anling",
    "411100": "Luohe",
    "513200": "Aba",
    "513300": "Garzê",
    "513400": "Liangshan",
    "522300": "Qianxinan",
    "522600": "Qiandongnan",
    "522700": "Qiannan",
    "532300": "Chuxiong",
    "532500": "Honghe",
    "532600": "Wenshan",
    "532800": "Xishuangbanna",
    "532900": "Dali",
    "533100": "Dehong",
    "533300": "Nujiang",
    "533400": "Shangri-La",
    "540100": "Lhasa",
    "540300": "Qamdo",
    "540400": "Nyingchi",
    "540600": "Nagqu",
    "542500": "Ngari",
    "610200": "Tongchuanshi",
    "610800": "Yulinshi",
    "623000": "Gannan",
    "632200": "Haibei",
    "632500": "Hainan",
    "632600": "Guoluo",
    "632800": "Haixi",
    "650100": "Ürümqi",
    "650200": "Karamay",
    "650400": "Turpan",
    "652300": "Changji",
    "652700": "Bortala",
    "652800": "Bayingolin",
    "652900": "Aksu",
    "653000": "Kizilsu",
    "653100": "Kashgar",
    "653200": "Hotan",
    "654000": "Ili",
    "654200": "Tacheng",
    "654300": "Altay",
}
MUNICIPALITIES = {
    "11": ("CN-BJ", "北京", "Beijing"),
    "12": ("CN-TJ", "天津", "Tianjin"),
    "31": ("CN-SH", "上海", "Shanghai"),
    "50": ("CN-CQ", "重庆", "Chongqing"),
}


def normalize(name):
    return re.sub("[^a-z]", "", unicodedata.normalize("NFKD", name.casefold()))


def main():
    with httpx.Client(timeout=30) as client:
        sources = {}
        for filename in ("province", "city"):
            response = client.get(f"{SOURCE}/packages/core/dist/{filename}.json")
            response.raise_for_status()
            sources[filename] = response.content
        license_response = client.get(f"{SOURCE}/LICENSE")
        license_response.raise_for_status()
    provinces = json.loads(sources["province"])
    source_cities = json.loads(sources["city"])
    region_states = json.loads((TARGET / "regions.json").read_text(encoding="utf-8"))[
        "states"
    ]
    original = json.loads(gzip.decompress((TARGET / "cities.json.gz").read_bytes()))
    state_map = {
        p["province"]: next(
            s["id"]
            for s in region_states
            if s["country_code"] == "CN" and s["name"] == p["name"]
        )
        for p in provinces
        if p["province"] not in ("71", "81", "82")
    }
    entries = []
    for province, (state_id, name, _english) in MUNICIPALITIES.items():
        source_cities.append(
            {"code": province + "0000", "province": province, "name": name + "市"}
        )
        state_map[province] = state_id
    for city in source_cities:
        # These are grouping labels, not destinations. Districts/towns are not imported.
        if city["code"][2:4] == "90":
            continue
        state_id = state_map.get(city["province"])
        if state_id is None:
            continue
        name = city["name"].removesuffix("市")
        match_name = MATCH_NAMES.get(city["code"], "".join(lazy_pinyin(name)))
        if city["province"] in MUNICIPALITIES:
            match_name = MUNICIPALITIES[city["province"]][2]
        matches = [
            row
            for row in original
            if row[2] == state_id and normalize(row[4]) == normalize(match_name)
        ]
        if city["code"] == "120000":
            selected = [
                -120000,
                "CN",
                "CN-TJ",
                name,
                "Tianjin",
                117.201,
                39.0842,
                "Asia/Shanghai",
            ]
        elif city["code"] == "310000":
            selected = [
                -310000,
                "CN",
                "CN-SH",
                name,
                "Shanghai",
                121.4737,
                31.2304,
                "Asia/Shanghai",
            ]
        elif matches:
            # Older core city records precede later district/duplicate additions.
            selected = min(matches, key=lambda row: row[0])
        else:
            raise ValueError(f"Unmatched city: {city['code']} {name} ({match_name})")
        entries.append(
            {
                "id": selected[0],
                "state_id": state_id,
                "name": name,
                "english_name": selected[4],
                "longitude": selected[5],
                "latitude": selected[6],
                "timezone": "Asia/Shanghai",
                "coordinate_system": "WGS84",
            }
        )
    if len({item["id"] for item in entries}) != len(entries):
        raise ValueError("Duplicate city IDs in normalized directory")
    output = {
        "source": "uiwjs/province-city-china",
        "revision": REVISION,
        "license": "MIT (names/levels); ODbL-1.0 (derived coordinates/IDs)",
        "source_sha256": {
            key: hashlib.sha256(value).hexdigest() for key, value in sources.items()
        },
        "scope": "Mainland prefecture-level destinations and four municipalities; not districts or towns.",
        "coordinates": "WGS84 reference centres from the existing snapshot, not verified hotel locations.",
        "cities": entries,
    }
    (TARGET / "china-cities.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (TARGET / "LICENSE.uiwjs.txt").write_text(
        license_response.content.decode("utf-8"), encoding="utf-8"
    )
    print(f"Wrote {len(entries)} mainland destinations")


if __name__ == "__main__":
    main()
