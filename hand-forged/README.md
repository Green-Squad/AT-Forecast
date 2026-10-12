# Hand-Forged Edition

Original Rails 5.0.7.1/Ruby 2.7 application, original locked gems, layouts and controllers. `HAND_FORGED_DEMO=true` isolates the application from production and replaces only external weather responses with deterministic local fixtures. The original Kelvin/wind transformations and database-update methods remain in `Shelter`. Demo-only ActiveAdmin parameter lists allow its existing CRUD forms to operate, and its shelter page has a sample refresh action. Email is captured in a session-scoped local inbox, using inline demo delivery rather than background work on a throttled instance; analytics is disabled.

`admin@handforged.invalid` / `HandForgedDemo!` is the synthetic administrator. The original JSON endpoints accept the synthetic key `hand-forged-demo`. Forecasts are sample data and must not be used for hiking decisions.

The PostgreSQL 15.19 sidecar initializes the original schema, 13 trail states, all 281 bundled shelters, seven daily and 56 three-hour forecasts per shelter. Dates are generated at instance startup. One bulk SQL restore avoids thousands of ORM inserts. Its TCP port opens after initialization; then the web startup checks seed counts before opening HTTP. Only the web container is public. The database data directory is a size-limited memory volume; application caches and local mail/files are also ephemeral. No production database host, user, credential, API token, persistent storage or managed database is used. Independent random application/Devise secrets are generated on startup and the demo has its own host-only session cookie.

Visitors on the same instance share edits. Closing a browser does not reset anything. Replacing/restarting the server restores the seed; deployments can briefly overlap instances with separate disposable state.

The separate `hand-forged` branch and Cloud Build trigger use `hand-forged/cloudbuild.yaml`. Its guard accepts only that branch and the fixed service name `atforecast-hand-forged`, project `at-forecast`, region `us-east4`. The two demo image packages reuse the project's existing registry. The service uses a dedicated runtime identity with no project roles, second-generation execution, request-based billing, min zero/max one and database-before-web startup ordering. Provision the runtime identity once; public access and the domain mapping are separate infrastructure setup.

## Validation

`bash hand-forged/check-container.sh` builds on the CI-created `hand-forged-web` and `hand-forged-db` images, checks HTML/JSON, demo login, CRUD/relationships and the original weather-refresh path, records startup/memory/image size, then replaces both containers and verifies database and file reset. PostgreSQL has no published port. The original production and Rust branches are not modified.

With min zero, idle compute is not billed, but request/startup compute, registry image storage, build minutes and traffic may incur charges. Existing account-wide free allowances are shared. See the measured validation artifact for resource sizing; do not assume guaranteed zero cost.
