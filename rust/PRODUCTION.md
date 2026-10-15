# Production Rust release

The default branch is `master`. Its Cloud Build trigger uses `cloudbuild.rust.yaml`,
runs isolated validation, and deploys `at-forecast-rust` in `us-east4`. Production
main/www mappings and the retained Rust subdomain use this service. `hand-forged`
has independent builds, credentials, database and routing.

After promotion, the old `rust` deployment trigger is disabled to prevent two
branches independently releasing production. GitHub validation still covers
Rust and master pushes/PRs. No automatic source synchronization is enabled.

Turso is authoritative for production writes. Do not run source sync after
promotion without reviewing source/destination ownership and conflicts.
Use a previous compatible immutable Rust revision/image for application rollback,
preserving Turso data. Switching to the old framework after new writes requires
explicit data reconciliation. Keep original services/data and protected snapshots
for at least 14 days after cutover; no automated destructive cleanup is configured.

Runtime host settings must be configured before routing: Game Loadouts uses
`ACCOUNT_BASE_URL=https://gameloadouts.com` and `PUBLIC_BASE_URL` with the same
origin. Cookies are origin-bound; existing Rust-preview users may need to log in
again. Main/www/Rust host cookies remain host-only. Existing imported password
hashes, confirmation/reset records and credentials are preserved.

AT Forecast's native weather job remains daily at 02:00 America/New_York.
Pause the original Rails updater only after production weather validation.
Do not modify the Hand-Forged services or retire original databases/VPS as part
of routine releases. Cloud credentials, private snapshots and production records
must never be committed.

## Android forecast ID compatibility

Stored and public forecast IDs use the original positive signed-32-bit contract.
Daily and hourly weather each continue their own Rails sequence. Persistent
per-table high-water marks, current maximum IDs and SQLite allocation metadata
prevent reuse after replacement/deletion. Counters advance atomically only with
successful weather publication. The job rejects exhaustion before publication,
keeping the last valid dataset. There is no separate native ID namespace or API
ID translation. Regression tests cover sequence continuation, retries, exhaustion,
client deserialization, dates and nulls. Never truncate an unchecked integer.

## Cloudflare and release cache keys

Cloud Build passes the exact commit SHA as the Docker ASSET_VERSION build arg.
Rendered local asset URLs carry ?v=<commit>; unversioned public-asset requests
(including relative font/image dependencies in stylesheets) redirect to a
release-keyed URL with no-store on the redirect. Successful versioned assets
retain explicit origin cache headers or default to public max-age=86400. Errors
are no-store. Content-addressed Rust uploads retain their existing immutable
URLs. HTML, account/admin responses and authenticated APIs remain no-store and
never use this asset middleware. robots.txt is revalidated without versioning.

Keep Cloudflare Caching Level Standard and include query strings in cache keys.
Do not create Cache Everything rules or ignore query strings. A deployment or
code rollback automatically changes/reuses the matching release cache key;
there is no purge token, scheduled invalidation or separate manual purge step.
Cached old releases expire naturally. Normal in-page data mutations retain their
existing behavior. An already-open page is not forcibly reloaded.
