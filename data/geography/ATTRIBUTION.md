# Geography data attribution

## Chinese destination normalization

`china-cities.json` restricts mainland dropdowns to 333 prefecture-level units
(including autonomous prefectures/leagues) and four municipalities, with Chinese
names. Districts, towns and aggregate labels such as “省直辖县级行政区划” are not
destinations in this list. Directly administered county-level destinations are not
yet exposed. This is a pinned directory snapshot, not a live administrative registry.

Names and administrative levels are derived from
[uiwjs/province-city-china](https://github.com/uiwjs/province-city-china), revision
`a708f0df0ebdb68f09f5fcf6345367f7d5e9a7dc`, under MIT; the full notice is preserved
in `LICENSE.uiwjs.txt`. The output records source file SHA256 hashes. Existing IDs
and WGS84 reference coordinates are joined from the ODbL snapshot below. Shanghai
and Tianjin have explicit local reference centres because the source lacks their
city records. Coordinates are approximate references, not hotel locations; live
AMap discovery resolves the actual selected-city location.

Regenerate with Python 3.12+, `httpx` and build-only `pypinyin==0.55.0` installed:

```powershell
backend/.venv/Scripts/python.exe scripts/sync_china_cities.py
```

The application reads these JSON files locally and caches them in memory. No
network search, user Key, or business database migration is needed for dropdowns.
Original city IDs remain resolvable so existing saved trips continue to load.

## Global directory

The geography snapshot is derived from [Countries States Cities Database](https://github.com/dr5hn/countries-states-cities-database), by the project contributors, under the [Open Database License v1.0](https://opendatacommons.org/licenses/odbl/1-0/).

This adapted database is also made available under ODbL-1.0. Attribution and database license apply to these data files; they do not change the license of independent application code.

The pinned source revision, source SHA-256 and row schema are recorded in `provenance.json`. To reproduce the adaptation, run `scripts/sync_geography.py`. The script retains subdivision relationships, display names, stable source city IDs, approximate WGS84 city centres and available timezone names, and drops unrelated fields.

The list is a community-maintained location directory, not a guarantee of map-service coverage. Administrative names and coverage may differ from providers. User-interface aliases do not alter the source identities.
