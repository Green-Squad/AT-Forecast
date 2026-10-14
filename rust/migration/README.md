# Rust Turso deployment and recovery

Only branch `rust`, service `at-forecast-rust` (project `at-forecast`, us-east4),
https://rust.atforecast.app, and the named Rust sync resources belong to this migration.
Original `master`, Rails service `at-forecast`, root/www routing, its weather job,
and `hand-forged` resources are protected and must not be changed.

The private `atforecast-rust-us` database uses the libSQL engine and official Rust
`libsql` 0.10 remote/TLS client with direct remote access. Group `rust-websites-us`
is in aws-us-east-1 (Virginia), near Cloud Run us-east4 (Northern Virginia).
No paid plan or overages were enabled. AWS imported databases currently reject
per-database size limits; account Free quotas and disabled overages remain active.
The Ireland database is retained as a frozen recovery copy, not an active sync target.

Runtime service account `rust-turso-web` can read only secret `rust-turso-read`.
The application uses that read-only database token. `rust-turso-sync` can access
`rust-turso-source` (source connection JSON) and `rust-turso-import` (destination
write token). Secret values, exports and private records must stay out of Git.
The original source credential permits more than reads, so the exporter forces
read-only sessions and a repeatable-read transaction; it never issues source
DDL or DML. Original credentials and jobs are unchanged.

## Repeatable export and import

`source_snapshot.py --config /protected/postgres-source.json --export
--output /protected/snapshot` inspects the live schema and takes a read-only
repeatable-read PostgreSQL snapshot. Preserve completed directories, mode 0700,
outside Git. Gzipped rows use explicit type tags; `schema.json` and `report.json`
record schema, counts and full normalized SHA256 sum/xor checksums.

`snapshot_sqlite.py --snapshot /protected/snapshot --database atforecast-rust-us
--token-file /protected/import.token --generation N --report /protected/report.json
--publish` converts only recognized declarations, imports bounded 150-row batches,
checks every original column, counts, checksums, uniqueness, foreign keys and
integrity, then atomically publishes the generation. An identical import can be
rerun; incomplete owned inactive staging can be recovered. Active or unrelated
data is not overwritten. New source declarations fail closed for review.

Decimals remain exact text, booleans integers, IDs unchanged, and naive timestamps
retain six-digit precision. Password hashes and API keys are unchanged and private.
Indexes include source indexes plus shelter/forecast route composites.

## Ongoing freshness

Job `at-forecast-rust-sync` in us-east4 runs using `Dockerfile.rust-sync`, one task,
600-second timeout, two task retries, and protected source/import credentials.
Refresh is **manual only**, as requested. The previously created Rust scheduler
remains PAUSED and provisioning never creates or enables a schedule. The original
daily weather updater remains unchanged. Rust forecast freshness is the last
successful manual source snapshot; it will not advance automatically.

Run and wait for a validated refresh:
`gcloud run jobs execute at-forecast-rust-sync --project=at-forecast --region=us-east4 --wait`.
Inspect the Cloud Run execution/logs for status `validated`, source snapshot time,
counts and freshness. Failures keep the previous valid dataset. A changed full
import measured about 42,459 indexed writes; source export takes about ten seconds.

The job validates snapshot checksums and complete forecast coverage (seven daily and forty hourly records per shelter, matching the observed source updater), builds a complete
private generation, validates it, and atomically changes the active pointer and
source fingerprints. Requests pin one generation in a read transaction. Partial
replacement, failure and retries retain the previous valid dataset. A 15-minute
lease prevents overlapping runs; the task timeout is shorter than the lease.
Two published generations are retained. The last 2,000 concise sync reports are
retained. Logs contain counts, duration, freshness and errors, not records.
Website caching behavior is unchanged: the existing homepage cache can add up to two hours of visible age. Shelter and authorized JSON routes read the fresh published generation.

## Validation and rollback

Run `python -m unittest discover -s rust/migration -p 'test_*.py' -v`.
CI also runs these against an isolated official libSQL server and exercises
PostgreSQL fixture import, route behavior, JSON authorization, query budgets,
DST, decimals, errors, retries, overlap and forecast replacement.

Application rollback only affects Rust:
`gcloud run services update-traffic at-forecast-rust --project at-forecast
--region us-east4 --to-revisions=PREVIOUS_TURSO_REVISION=100`.
Revision `at-forecast-rust-00012-xlx` is the verified Virginia baseline. Older Ireland
revisions point to a frozen database and should not be used for a normal app rollback. The older
`at-forecast-rust-00006-dp5` remains available as a legacy read-only source revision.

For a data rollback, pause only `at-forecast-rust-sync-schedule`, inspect private
`_migration_reports`, and change `_migration_state.generation` to the retained
previous validated generation using the Rust import credential. Verify Rust
HTML/JSON, then run a manual refresh after addressing the bad input. Keep automatic scheduling paused. Do not
modify source data or original resources. Recovery snapshots are retained in the
migration operator's protected workspace outside both repositories.

Cloud Run reserves some paths ending in `z`, including the observed external
`/healthz` 404; container tests exercise that existing route, while deployed smoke
checks use HTML and JSON. See https://docs.cloud.google.com/run/docs/known-issues.

## Virginia relocation recovery and validation

The full Turso export plus WAL was validated against every live physical table,
schema/index, sequence and normalized checksum before import; the new Virginia
endpoint passed the same checks. This includes prior generations, source baselines,
sync reports, conflicts and Rust-owned data, not only original source tables.
Protected SQLite recovery files remain outside Git in private/virginia-recovery
in the migration workspace. Frozen Ireland copies remain private and source-independent.
New read/import tokens are Secret Manager version 2; Game Loadouts' scoped writer
is version 5. Source secret version 1 is unchanged. Both manual sync jobs are in us-east4; deployment workflows target only that Rust region.
Hrana BLOB decoding accepts the endpoint's padded and unpadded Base64 forms and has
a focused regression test. The active website routes and independent writes remain enabled.


## Native Rust daily weather fetch

The separate `at-forecast-rust-weather` job uses the web image's
`/usr/local/bin/at-forecast-weather` binary, with the import credential and a
separate Secret Manager OpenWeather key. The web service has neither credential.
It fetches the same seven-day daily and forty-step three-hour forecasts as Rails,
using the existing provider subscription without changing plans. Calls are
spaced at least 2.1 seconds apart and rate-limit responses stop the run. Network
timeouts and the total job deadline are bounded. Credentials, request URLs,
provider payloads and user records are excluded from production logs.

The intended daily schedule is 00:30 America/New_York, after the original daily
updater. It uses only Rust resources and the private Virginia Turso database.
The source-database sync remains manual. All source refreshes preserve
provider-owned forecasts after the first successful weather publication;
source-deleted shelters also remove their destination forecasts. Original
Rails jobs/databases remain unchanged. Imported users, keys and other tables are
copied unchanged into every newly validated generation.

Publication is atomic only after every shelter has complete daily/hourly
coverage and all copied table counts, foreign keys and SQLite integrity pass.
A shared lease prevents overlap with manual sync. Failed staging can be retried
safely in foreign-key dependency order. Two complete generations and protected
migration exports remain available for recovery. Weather IDs use the Rust-only
namespace starting at 4294967296. Homepage caching retains its existing two-hour
behavior; shelter HTML and authenticated JSON read the active generation.

Run manually: `gcloud run jobs execute at-forecast-rust-weather --project=at-forecast --region=us-east4 --wait`.
To stop only this new schedule: `gcloud scheduler jobs pause at-forecast-rust-weather-daily --project=at-forecast --location=us-east4`.
