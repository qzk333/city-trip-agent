"""Build a pinned, compact geography snapshot; no API credentials required."""

import gzip
import hashlib
import json
from pathlib import Path

import httpx

REVISION = "48f586f2d068e4d9ea0e50eafc16c3d0d7190070"
SOURCE = f"https://raw.githubusercontent.com/dr5hn/countries-states-cities-database/{REVISION}"
TARGET = Path(__file__).resolve().parents[1] / "data/geography"


def main():
    response = httpx.get(f"{SOURCE}/json/countries+states+cities.json", timeout=90)
    response.raise_for_status()
    countries, states, cities = [], [], []
    for country in response.json():
        code = country["iso2"]
        countries.append(
            {
                "code": code,
                "name": country.get("translations", {}).get("zh-CN") or country["name"],
                "english_name": country["name"],
            }
        )
        for state in country.get("states", []):
            state_code = (
                state.get("state_code") or state.get("iso2") or str(state["id"])
            )
            state_id = f"{code}-{state_code}"
            states.append(
                {
                    "id": state_id,
                    "country_code": code,
                    "name": state.get("translations", {}).get("zh-CN")
                    or state.get("native")
                    or state["name"],
                }
            )
            for city in state.get("cities", []):
                lat, lon = city.get("latitude"), city.get("longitude")
                if lat is None or lon is None:
                    continue
                label = (
                    city.get("translations", {}).get("zh-CN")
                    or city.get("native")
                    or city["name"]
                )
                cities.append(
                    [
                        city["id"],
                        code,
                        state_id,
                        label,
                        city["name"],
                        float(lon),
                        float(lat),
                        city.get("timezone") or state.get("timezone") or "",
                    ]
                )
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "regions.json").write_text(
        json.dumps(
            {"countries": countries, "states": states},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    packed = json.dumps(cities, ensure_ascii=False, separators=(",", ":")).encode()
    (TARGET / "cities.json.gz").write_bytes(gzip.compress(packed, mtime=0))
    metadata = {
        "source": "dr5hn/countries-states-cities-database",
        "revision": REVISION,
        "license": "ODbL-1.0",
        "source_sha256": hashlib.sha256(response.content).hexdigest(),
        "country_count": len(countries),
        "state_count": len(states),
        "city_count": len(cities),
        "city_schema": [
            "id",
            "country_code",
            "state_id",
            "name",
            "english_name",
            "longitude",
            "latitude",
            "timezone",
        ],
        "coordinates": "WGS84",
    }
    (TARGET / "provenance.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata))
    for label in ("Hangzhou", "Shanghai", "Paris", "Tokyo"):
        print(
            json.dumps(
                next((c for c in cities if c[4] == label), None), ensure_ascii=True
            )
        )


if __name__ == "__main__":
    main()
