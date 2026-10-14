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
