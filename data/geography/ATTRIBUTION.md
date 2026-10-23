# Geography data attribution

The geography snapshot is derived from [Countries States Cities Database](https://github.com/dr5hn/countries-states-cities-database), by the project contributors, under the [Open Database License v1.0](https://opendatacommons.org/licenses/odbl/1-0/).

This adapted database is also made available under ODbL-1.0. Attribution and database license apply to these data files; they do not change the license of independent application code.

The pinned source revision, source SHA-256 and row schema are recorded in `provenance.json`. To reproduce the adaptation, run `scripts/sync_geography.py`. The script retains subdivision relationships, display names, stable source city IDs, approximate WGS84 city centres and available timezone names, and drops unrelated fields.

The list is a community-maintained location directory, not a guarantee of map-service coverage. Administrative names and coverage may differ from providers. User-interface aliases do not alter the source identities.
