# AT Forecast: Rust baseline without query optimizations

This branch rewrites the public reader in Axum, SQLx/PostgreSQL, and Askama. It starts from Rails commit `a19164aa62457bc64856f004710e4ec8950dd0b8`. The root Dockerfile builds Rust; `Dockerfile.rails` preserves the original web container. Rails source, schema, migrations, and `Dockerfile-sync` remain available and unchanged. Do not run `Dockerfile-init` or migrations against the existing database.

## What works

- Homepage, state averages and expandable shelter lists, mileage and GPS lookup, shelter forecasts, previous/next links, About/privacy policy, and public error handling.
- Existing Android JSON API: `/index.json?api_key=...`, optional `include_shelters=true`, `/shelters/:id.json?api_key=...`, and optional `dist_miles`. CORS permits public GET requests. Keys are checked against the existing `keys` table on every JSON request; cached JSON files are never publicly served.
- Original ERB markup converted to Askama, original public CSS/icons, and the original inline homepage JavaScript. Metadata, analytics ID, weather categories, freeze warning threshold, dates, decimal strings, 2193-mile southbound calculation, and API field names retained. Set `ENABLE_ANALYTICS=false` during comparisons.
- Read-only database access, connection pool, query/connection-acquisition timeouts, structured logs without request URLs or parameter values, static content types/cache headers, `/healthz`, and SIGTERM/SIGINT shutdown.

Rust does not implement admin/login, schema initialization, data import, or weather refresh. Keep the existing Rails refresh job running against the same database. Public requests do not call OpenWeatherMap.

## Deliberately unchanged query work

This is **not** the batched implementation proposed earlier. No preloading, joined forecast query, database-side aggregate, GPS index/catalog cache, new pagination, or new page-serving cache was introduced.

| Route | Data statements per uncached request |
| --- | --- |
| Homepage HTML, S states | `1 + S + S`: states, one average query per state, one ordered shelter query per state |
| Index JSON without shelters | `1 + 1 + S`: API key, states, one average query per state |
| Index JSON with shelters | Above plus `S` shelter queries |
| Shelter HTML, D forecast days, N nonempty hourly days | `1 + 1 + D + N + 2`: shelter, daily forecasts, per-day emptiness checks, per-nonempty-day hourly reads, two neighbors |
| Single shelter JSON | `1 + 1 + 1 + D`: shelter, key, daily forecasts, one hourly query per day |
| Distance JSON | Anchor, key, range query, then a daily query and per-day hourly queries **for every returned shelter** |
| GPS lookup HTML | Full shelter SELECT and Haversine scan, then the same forecast/neighbor work |

Repeated previous/next calls use a **request-local** cache, matching the existing Rails query cache rather than adding a cross-request optimization. `X-DB-Query-Cache-Hits` reports these hits. SQLx prepared statements and ActiveRecord query caching are different mechanisms; compare actual query counts, not just method calls. Templates have no I/O; their existing database work happens before rendering, with the same unbatched query shapes. The relative scheduling of some template queries differs.

State averages still load all qualifying rows and divide integer totals in application code; negative division rounds down as in Ruby. The original cutoff includes all historical weather through today, not just today's forecast. State/GPS/distance queries retain their original unspecified order; explicit mileage/date orders are retained. No result lists are truncated.

`CACHE_MODE=rails` (default) preserves the homepage file cache with the original `floor(age_seconds) > 7200` expiry test. Shelter responses are written to cache files but **not read**, matching the repository's plain Rails container (no reverse-proxy cache rewrite is supplied). JSON authentication is never bypassed. Files are ephemeral under `/tmp/at-forecast-cache`; the homepage cache has one entry, and shelter files overwrite the existing ID/format path. Entries over 8 MiB are not cached; their complete responses still render. File cache has the same cross-instance invalidation limitation as Rails: a separate refresh job cannot delete a web container's local files. `CACHE_MODE=off` disables all file-cache reads/writes independently. There is no permanent in-memory page cache or shared cache.

Differences unrelated to SQL batching: SQL is parameterized, malformed mileage/coordinates return 400, invalid IDs return 404, missing forecasts/database failures return 503 instead of an application exception, links use relative URLs, CSRF/session metadata is omitted from this GET-only reader, and serialization/template whitespace and cache-file compression differ. Rails' unused gzip page-cache artifacts are not produced. These are not a claim of a perfectly language-only microbenchmark; response bytes and DB counts are recorded explicitly.

## Database assumptions

Production database/cloud credentials were not supplied. The reader schema is inferred from `db/schema.rb`: PostgreSQL integer IDs, unbounded numeric mileage/coordinates, timestamp-without-time-zone weather dates, and the existing shelter/state/forecast relationships. Production data, PostgreSQL version, NULL values, deployment `TZ`, and media packaging remain unverified. Like the current templates, the reader requires populated mileage, weather dates, daily temperatures, and daily descriptions. Nullable shelter names/elevations/coordinates and hourly temperatures/descriptions/wind are preserved as JSON null and blank HTML; GPS nil coordinates become zero as in Ruby. Other unexpected NULLs fail safely with 503 rather than fabricate forecasts. States are expected to have distinct names and daily rows distinct dates; duplicate names/dates can expose differences in Rails hash/request-cache behavior.

Rails sets `config.time_zone` to Eastern and `active_record.default_timezone` to `:local`. A standard Ruby container has system time UTC, so Rust defaults `DATABASE_TIMEZONE=UTC` and `DISPLAY_TIMEZONE=America/New_York`: stored timestamps are interpreted as UTC and displayed/bounded by Eastern days. **Confirm the actual Rails container TZ and existing timestamp values**; set `DATABASE_TIMEZONE` to its storage/system timezone if different. Local storage timestamps during DST overlap choose the earlier occurrence; nonexistent local timestamps require data verification. Averages use the storage/system timezone's end of today, matching `Time.now.end_of_day`. JSON timestamps retain millisecond precision and Eastern offsets.

There are no uploaded forecast images in the inspected application: weather output is text/CSS, icons and public assets are tracked. The runtime copies tracked public assets. It does not assume untracked `public/cached_pages` files exist. If production adds untracked assets, package those separately after inspection.

## Local run

From repository root, supply a **read-only** database URL through your shell/secret manager, then:

```sh
export DATABASE_URL='postgres://USER:PASSWORD@HOST/DATABASE?sslmode=require'
CACHE_MODE=off ENABLE_ANALYTICS=false cargo run --locked --release --manifest-path rust/Cargo.toml
curl -i http://127.0.0.1:8080/healthz
```

The URL above is a placeholder; do not commit your real credentials. SQLx compilation does not access a database and no migration runs at startup. Existing Rails variables are also supported: `FASTATWEATHER_DATABASE_HOST`, `FASTATWEATHER_DATABASE_USERNAME`, `FASTATWEATHER_DATABASE_PASSWORD`, optional `FASTATWEATHER_DATABASE_NAME` (default `fast_at_weather_production`). `DATABASE_URL` takes precedence and supports PostgreSQL TLS/socket configuration, including Cloud SQL proxy sockets.

| Variable | Default |
| --- | --- |
| `PORT` | `8080`, listens on `0.0.0.0` |
| `DB_POOL_SIZE` | `5`, valid 1–100 |
| `DB_ACQUIRE_TIMEOUT_MS` | `5000`, includes connecting when acquiring |
| `QUERY_TIMEOUT_MS` | `5000`, both client timeout and server `statement_timeout` |
| `CACHE_MODE` | `rails`; use `off` for uncached comparisons |
| `CACHE_DIR` | `/tmp/at-forecast-cache` |
| `PUBLIC_DIR` | `public` locally, `/app/public` in image |
| `DATABASE_TIMEZONE` | `UTC` |
| `DISPLAY_TIMEZONE` | `America/New_York` |
| `ENABLE_ANALYTICS` | `true`; set `false` for tests/comparisons |
| `RUST_LOG` | application info, SQLx off |

The pool requests `default_transaction_read_only=on`. Also use a SELECT-only database role; only the five reader tables require SELECT. `/healthz` is a lightweight process check, not database readiness. The lazy pool connects on the first data request, which must be included when measuring a data cold start.

## Isolated validation

Use an empty **test-only** database. Never run the fixture SQL against existing data. Example with Docker:

```sh
docker run -d --name at-forecast-db -p 127.0.0.1:5432:5432 \
  -e POSTGRES_PASSWORD=fixture-admin-only -e POSTGRES_DB=at_forecast_test postgres:17
docker exec at-forecast-db pg_isready -U postgres -d at_forecast_test
# Wait until ready, then load the representative schema/data and SELECT-only role.
docker exec -i at-forecast-db psql -U postgres -d at_forecast_test -v ON_ERROR_STOP=1 < rust/tests/fixture.sql
export DATABASE_URL='postgres://forecast_reader:fixture-only@127.0.0.1:5432/at_forecast_test'
AT_FORECAST_TEST_DATABASE=isolated cargo test --locked --manifest-path rust/Cargo.toml -- --nocapture
cargo clippy --locked --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo fmt --manifest-path rust/Cargo.toml --check
```

The explicit `AT_FORECAST_TEST_DATABASE=isolated` opt-in prevents integration tests accidentally using a production URL. Without it the integration test announces that PostgreSQL validation was skipped; unit tests still run. Fixtures cover negative averages, future-day cutoff, unordered distance ranges, escaped names, Eastern midnight boundaries, absent hourly data, complete forecasts, lookup relationships, API authorization, read-only writes rejected, cache behavior, static CSS, missing records, and injection rejection. Query-count assertions preserve increasing work: fixture shelter HTML uses 8 statements; single JSON uses 6; two-shelter distance JSON uses 9. CI runs these with PostgreSQL 17, builds/smoke-tests the non-root image, checks shutdown, and compares with the actual Rails handlers/templates on the same fixture.

## Image and separate Cloud Run service

```sh
docker build -t at-forecast-rust .
docker run --rm -p 8080:8080 --env-file /path/to/private/runtime.env at-forecast-rust
```

The production image is a multi-stage release build with a Debian trixie-slim runtime, CA certificates, Rustls, user `10001:10001`, public assets, and the executable. No Ruby, Cargo, source credentials, migrations, or persistent volume is required. Use linux/amd64 when building on an ARM workstation for Cloud Run.

Deploy from this branch **as a new service**, using your existing project/region, Cloud SQL instance, networking, and a Secret Manager secret containing a SELECT-only URL:

```sh
gcloud run deploy at-forecast-rust --source . --region REGION --project PROJECT \
  --cpu 1 --memory 512Mi --concurrency 5 --min-instances 0 --max-instances 2 \
  --set-env-vars CACHE_MODE=off,ENABLE_ANALYTICS=false,DATABASE_TIMEZONE=UTC \
  --set-secrets DATABASE_URL=at-forecast-readonly-url:latest \
  --add-cloudsql-instances PROJECT:REGION:INSTANCE --allow-unauthenticated
```

Cloud SQL can use a URL such as `postgresql://USER:PASSWORD@/DATABASE?host=/cloudsql/PROJECT:REGION:INSTANCE`, stored only in the secret. Grant the runtime service account Cloud SQL Client and access to that secret. If using a private IP, attach the same VPC/subnet/egress setup as Rails. If the existing database is external, omit the Cloud SQL flag and use equivalent networking/TLS. Use the new `run.app` URL for comparisons; add a separate subdomain only after validating it. Do not change the Rails service or scheduled Rails refresh job. Cloud deployment, billing, and domain mapping are not performed by this branch.

## Reproducible comparison

Use isolated services, equivalent full data snapshots, CPU/memory, pool size, timezone, database proximity, network, and cache settings. Do not load-test the live site. Begin with **both caches off**, then run a separate warm homepage-cache comparison. The Rails comparison wrapper only disables file caching and adds metrics; it retains the original per-state/per-day queries and templates. It is for isolated comparisons, not deployment.

```sh
# Run Rust with the fixture URL and CACHE_MODE=off as above.
# Original Rails image, sharing the same local database via host networking on Linux:
docker build -f Dockerfile.rails -t at-forecast-rails .
docker run --rm --network host -e DATABASE_URL -e AT_FORECAST_TEST_DATABASE=isolated \
  -e CACHE_MODE=off -e PORT=8082 -e RAILS_ENV=production \
  -e SECRET_KEY_BASE=isolated-comparison-only at-forecast-rails \
  bundle exec rails runner tools/rails_compare_server.rb
# In another terminal:
python3 tools/compare.py --rails http://127.0.0.1:8082 --rust http://127.0.0.1:8080
mkdir -p benchmark-results
python3 tools/benchmark.py --base http://127.0.0.1:8080 --requests 200 --concurrency 1 > benchmark-results/rust-c1.json
python3 tools/benchmark.py --base http://127.0.0.1:8080 --requests 200 --concurrency 5 > benchmark-results/rust-c5.json
python3 tools/benchmark.py --base http://127.0.0.1:8082 --requests 200 --concurrency 1 > benchmark-results/rails-c1.json
python3 tools/benchmark.py --base http://127.0.0.1:8082 --requests 200 --concurrency 5 > benchmark-results/rails-c5.json
# Supply BENCH_API_KEY through environment, not command arguments or output:
python3 tools/benchmark.py --route '/shelters/2.json?api_key={API_KEY}' --concurrency 5
```

Replace fixture IDs/mileages with matching real-snapshot requests. The benchmark records warm p50/p95, query counts, mean query time (including pool wait for Rust), bytes, and page-cache status. Each request opens a fresh connection, uses identity encoding and `Cache-Control: no-cache`; the Rust responses also use `no-store`. No CDN/browser cache is involved. Ten warmups are excluded; concurrency 1 and 5 are separate tests. Add explicit `--allow-isolated-host` only for a dedicated comparison host. Do not put real API keys in route arguments: use the placeholder.

For resource measurements use `docker stats --no-stream CONTAINER`, `docker image inspect IMAGE --format '{{.Size}}'`, and `/proc/PID/status` for process RSS. Report Docker memory and process RSS separately. Time container launch until `/healthz`, then separately until the first database-backed request; preserve the structured `ready_ms` log. Local process startup is not a Cloud Run cold start. The CI artifact measures local Docker-to-health elapsed time, including Docker overhead, and records image/memory plus latency samples on a tiny synthetic dataset; it is not evidence of a production speedup.

For Cloud Run repeat on two dedicated services with identical allocation, concurrency, min/max instances, billing mode, and database. Record first request after a **confirmed new instance**, its instance startup log and first DB request separately from warm requests. Obtain billable instance time from Cloud Monitoring metric `run.googleapis.com/container/billable_instance_time` over identical test windows, label-filtered by service and revision, and align with request counts/autoscaling. Query timings, cloud cold starts, and billed time must be measured; do not substitute local startup or client latency. No Cloud Run measurements or Rails-versus-Rust speedup are asserted without those results. Query counts should match; any mismatch is a comparison defect to investigate before attributing a gain to Rust.
