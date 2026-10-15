pub mod asset_cache;
pub mod db;
pub mod forecast_id;
pub mod remote;
pub mod weather;
use askama::Template;
use axum::{
    Json, Router,
    extract::{Query, State},
    http::{HeaderValue, StatusCode, Uri},
    middleware,
    response::{Html, IntoResponse, Response},
    routing::get,
};
use chrono_tz::Tz;
use db::{DayView, Db, Shelter, StateView};
use serde_json::json;

use std::{
    collections::HashMap,
    env,
    path::PathBuf,
    time::{Duration, Instant, SystemTime},
};
use tower::Layer;
use tower_http::{
    cors::{Any, CorsLayer},
    services::ServeDir,
    set_header::SetResponseHeaderLayer,
};

#[derive(Clone)]
pub struct Config {
    pub query_timeout: Duration,
    pub cache: bool,
    pub cache_dir: PathBuf,
    pub public_dir: PathBuf,
    pub database_timezone: Tz,
    pub display_timezone: Tz,
    pub analytics: bool,
}
impl Config {
    pub fn from_env() -> Result<Self, String> {
        let tz = |name: &str, default: &str| {
            env::var(name)
                .unwrap_or_else(|_| default.into())
                .parse::<Tz>()
                .map_err(|_| format!("Invalid {name}"))
        };
        let mode = env::var("CACHE_MODE").unwrap_or_else(|_| "rails".into());
        if mode != "rails" && mode != "off" {
            return Err("CACHE_MODE must be rails or off".into());
        }
        Ok(Self {
            query_timeout: Duration::from_millis(number("QUERY_TIMEOUT_MS", 5000)?),
            cache: mode == "rails",
            cache_dir: env::var("CACHE_DIR")
                .unwrap_or_else(|_| "/tmp/at-forecast-cache".into())
                .into(),
            public_dir: env::var("PUBLIC_DIR")
                .unwrap_or_else(|_| "public".into())
                .into(),
            database_timezone: tz("DATABASE_TIMEZONE", "UTC")?,
            display_timezone: tz("DISPLAY_TIMEZONE", "America/New_York")?,
            analytics: env::var("ENABLE_ANALYTICS").unwrap_or_else(|_| "true".into()) == "true",
        })
    }
}
fn number(name: &str, default: u64) -> Result<u64, String> {
    let n = env::var(name)
        .unwrap_or_else(|_| default.to_string())
        .parse::<u64>()
        .map_err(|_| format!("Invalid {name}"))?;
    if n == 0 {
        Err(format!("{name} must be positive"))
    } else {
        Ok(n)
    }
}
pub async fn pool_from_env(cfg: &Config) -> Result<remote::Pool, String> {
    remote::Pool::from_env(cfg.query_timeout).await
}
#[derive(Clone)]
pub struct App {
    pub pool: remote::Pool,
    pub config: Config,
}
pub fn router(app: App) -> Router {
    let assets = ServeDir::new(app.config.public_dir.join("assets"));
    let icons = ServeDir::new(app.config.public_dir.clone());
    let asset_headers = middleware::from_fn(asset_cache::serve);
    // Only public static paths are reachable: never expose Rails' cached_pages API responses.
    Router::new()
        .route("/", get(handle))
        .route("/index.json", get(handle))
        .route("/.json", get(handle))
        .route("/about", get(handle))
        .route("/shelters/{id}", get(handle))
        .route("/nearest_shelter", get(handle))
        .route("/healthz", get(|| async { Json(json!({"status":"ok"})) }))
        .nest_service("/assets", asset_headers.clone().layer(assets))
        .route_service("/favicon.ico", asset_headers.clone().layer(icons.clone()))
        .route_service(
            "/robots.txt",
            SetResponseHeaderLayer::if_not_present(
                axum::http::header::CACHE_CONTROL,
                HeaderValue::from_static("no-cache"),
            )
            .layer(icons.clone()),
        )
        .route_service(
            "/apple-touch-icon.png",
            asset_headers.clone().layer(icons.clone()),
        )
        .route_service(
            "/apple-touch-icon-precomposed.png",
            asset_headers.layer(icons),
        )
        .fallback(
            |uri: Uri| async move { public_error(Error::NotFound, uri.path().ends_with(".json")) },
        )
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods([axum::http::Method::GET])
                .allow_headers(Any),
        )
        .with_state(app)
}
#[derive(Debug)]
pub enum Error {
    NotFound,
    BadRequest,
    Unauthorized,
    Unavailable,
    Render,
}
impl IntoResponse for Error {
    fn into_response(self) -> Response {
        match self {
            Self::Unauthorized => (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error":"Access denied"})),
            )
                .into_response(),
            other => {
                let (code, message) = match other {
                    Self::NotFound => (
                        StatusCode::NOT_FOUND,
                        "The page or shelter you requested could not be found.",
                    ),
                    Self::BadRequest => (
                        StatusCode::BAD_REQUEST,
                        "Check the mileage, coordinates, or shelter ID and try again.",
                    ),
                    _ => (
                        StatusCode::SERVICE_UNAVAILABLE,
                        "The forecast is temporarily unavailable. Please try again shortly.",
                    ),
                };
                (code,Html(format!("<!doctype html><html><head><title>AT Forecast</title></head><body><h1>AT Forecast</h1><p>{message}</p><a href=\"/\">Return home</a></body></html>"))).into_response()
            }
        }
    }
}
fn public_error(error: Error, json_format: bool) -> Response {
    if !json_format || matches!(error, Error::Unauthorized) {
        return error.into_response();
    }
    let status = match error {
        Error::NotFound => StatusCode::NOT_FOUND,
        Error::BadRequest => StatusCode::BAD_REQUEST,
        _ => StatusCode::SERVICE_UNAVAILABLE,
    };
    (status, Json(json!({"status": status.as_u16(), "error": status.canonical_reason().unwrap_or("Error")}))).into_response()
}

#[derive(Template)]
#[template(path = "home.html")]
struct HomeTemplate {
    asset_version: &'static str,
    states: Vec<StateView>,
    analytics: bool,
}
#[derive(Template)]
#[template(path = "shelter.html")]
struct ShelterTemplate {
    asset_version: &'static str,
    shelter: Shelter,
    days: Vec<DayView>,
    previous: Option<Shelter>,
    next: Option<Shelter>,
    updated: String,
    freeze: bool,
    analytics: bool,
}
#[derive(Template)]
#[template(path = "about.html")]
struct AboutTemplate {
    asset_version: &'static str,
    analytics: bool,
}
fn render(t: impl Template) -> Result<Response, Error> {
    Ok(Html(t.render().map_err(|_| Error::Render)?).into_response())
}

async fn handle(
    State(app): State<App>,
    uri: Uri,
    Query(params): Query<HashMap<String, String>>,
    headers: axum::http::HeaderMap,
) -> Response {
    let start = Instant::now();
    let mut db = Db::new(app.pool.clone(), app.config.query_timeout);
    let wants_json = uri.path().ends_with(".json")
        || headers
            .get("accept")
            .and_then(|h| h.to_str().ok())
            .is_some_and(|v| {
                v.split(',')
                    .next()
                    .is_some_and(|v| v.trim() == "application/json")
            });
    let route = if uri.path().starts_with("/shelters/") {
        "shelter"
    } else if uri.path() == "/nearest_shelter" {
        "nearest"
    } else if uri.path() == "/about" {
        "about"
    } else {
        "index"
    };
    let mut cache_hit = false;
    let result = dispatch(&app, &mut db, &uri, &params, wants_json, &mut cache_hit).await;
    let mut response = match result {
        Ok(r) => r,
        Err(e) => public_error(e, wants_json),
    };
    let metrics = &db.metrics;
    response
        .headers_mut()
        .insert("x-db-query-count", metrics.queries.into());
    response
        .headers_mut()
        .insert("x-db-query-cache-hits", metrics.query_cache_hits.into());
    if let Ok(v) = HeaderValue::from_str(&format!("db;dur={:.3}", metrics.db_ms)) {
        response.headers_mut().insert("server-timing", v);
    }
    response.headers_mut().insert(
        "x-page-cache",
        HeaderValue::from_static(if cache_hit {
            "hit"
        } else if app.config.cache {
            "miss"
        } else {
            "off"
        }),
    );
    response
        .headers_mut()
        .insert("cache-control", HeaderValue::from_static("no-store"));
    tracing::info!(
        route,
        format = if wants_json { "json" } else { "html" },
        status = response.status().as_u16(),
        queries = metrics.queries,
        query_cache_hits = metrics.query_cache_hits,
        db_ms = metrics.db_ms,
        elapsed_ms = start.elapsed().as_secs_f64() * 1000.0,
        page_cache_hit = cache_hit,
        "request"
    );
    response
}
async fn dispatch(
    app: &App,
    db: &mut Db,
    uri: &Uri,
    p: &HashMap<String, String>,
    json_format: bool,
    cache_hit: &mut bool,
) -> Result<Response, Error> {
    let cfg = &app.config;
    match uri.path() {
        "/" | "/index.json" | "/.json" => {
            if json_format {
                if !db.auth(p.get("api_key").map(String::as_str)).await? {
                    return Err(Error::Unauthorized);
                }
                let include = p.get("include_shelters").is_some_and(|v| v == "true");
                let states = db.states(include, cfg).await?;
                let output:Vec<_>=states.iter().map(|s|{
                    let mut v=json!({"state_id":s.id,"name":s.name,"average_high":s.high,"average_low":s.low});
                    if include {v["shelters"]=json!(s.shelters.iter().map(Shelter::catalog_json).collect::<Vec<_>>());}v
                }).collect();
                Ok(Json(output).into_response())
            } else {
                if cfg.cache
                    && let Some(html) = read_home(cfg).await
                {
                    *cache_hit = true;
                    return Ok(Html(html).into_response());
                }
                let html = HomeTemplate {
                    asset_version: asset_cache::VERSION,
                    states: db.states(true, cfg).await?,
                    analytics: cfg.analytics,
                }
                .render()
                .map_err(|_| Error::Render)?;
                if cfg.cache {
                    write_cache(cfg, "index.html", html.as_bytes()).await;
                }
                Ok(Html(html).into_response())
            }
        }
        "/about" => render(AboutTemplate {
            asset_version: asset_cache::VERSION,
            analytics: cfg.analytics,
        }),
        "/nearest_shelter" => {
            let s = if let Some(mile) = p.get("nobo_mile").filter(|v| !v.trim().is_empty()) {
                db.nearest_mile(mile.parse().map_err(|_| Error::BadRequest)?)
                    .await?
            } else if let Some(coords) = p.get("coords").filter(|v| !v.trim().is_empty()) {
                let parts: Vec<_> = coords.split(',').collect();
                if parts.len() != 2 {
                    return Err(Error::BadRequest);
                }
                let lat = finite(parts[0])?;
                let lon = finite(parts[1])?;
                if lat.abs() > 90.0 || lon.abs() > 180.0 {
                    return Err(Error::BadRequest);
                }
                db.nearest_coords(lat, lon).await?
            } else {
                return Ok((StatusCode::FOUND, [("location", "/")], "").into_response());
            };
            shelter_html(db, s, cfg, false).await
        }
        path if path.starts_with("/shelters/") => {
            let id = path
                .trim_start_matches("/shelters/")
                .trim_end_matches(".json")
                .parse::<i32>()
                .map_err(|_| Error::NotFound)?;
            let s = db.shelter(id).await?;
            let response = if json_format {
                if !db.auth(p.get("api_key").map(String::as_str)).await? {
                    return Err(Error::Unauthorized);
                }
                let shelters = if let Some(distance) = p.get("dist_miles") {
                    // Ruby String#to_f accepts a numeric prefix, invalid/empty text becomes 0.0.
                    db.ahead(
                        &s,
                        ruby_float(distance)
                            .to_string()
                            .parse()
                            .map_err(|_| Error::BadRequest)?,
                    )
                    .await?
                } else {
                    vec![s]
                };
                let shelter_ids: Vec<i32> = shelters.iter().map(|s| s.id).collect();
                let mut forecasts = db.days_many(&shelter_ids, cfg).await?;
                let mut output = Vec::new();
                for s in shelters {
                    let days = forecasts.remove(&s.id).unwrap_or_default();
                    output.push(json!({"shelter_id":s.id,"name":s.name,"mileage":s.mileage_text(),"elevation":s.elevation,
                        "southbound_mileage":s.southbound(),"daily_weather":days.iter().map(|d|d.json(cfg)).collect::<Result<Vec<_>,_>>()? }));
                }
                // Match caches_page writes but never read these files or bypass API authentication.
                if cfg.cache {
                    write_cache(
                        cfg,
                        &format!("shelters/{id}.json"),
                        serde_json::to_string(&output)
                            .map_err(|_| Error::Render)?
                            .as_bytes(),
                    )
                    .await;
                }
                Json(output).into_response()
            } else {
                let response = shelter_html(db, s, cfg, true).await?;
                // HTML cache writes occur inside shelter_html after successful rendering.
                response
            };
            Ok(response)
        }
        _ => Err(Error::NotFound),
    }
}
fn finite(s: &str) -> Result<f64, Error> {
    let x = s.trim().parse::<f64>().map_err(|_| Error::BadRequest)?;
    if x.is_finite() {
        Ok(x)
    } else {
        Err(Error::BadRequest)
    }
}
fn ruby_float(s: &str) -> f64 {
    let s = s.trim_start();
    for end in (1..=s.len()).rev().filter(|i| s.is_char_boundary(*i)) {
        if let Ok(v) = s[..end].parse::<f64>() {
            return if v.is_finite() { v } else { 0.0 };
        }
    }
    0.0
}
async fn shelter_html(
    db: &mut Db,
    s: Shelter,
    cfg: &Config,
    write_page_cache: bool,
) -> Result<Response, Error> {
    let days = db.days(&s, cfg).await?;
    let Some(first) = days.first() else {
        return Err(Error::Unavailable);
    };
    let freeze = first.daily.low < 35;
    let updated = db::displayed(first.daily.created_at, cfg)
        .format("%A %B %-d, %Y at %l:%M %p")
        .to_string();
    // Keep the ERB's repeated method calls; identical neighbor reads hit Rails-equivalent request cache.
    let previous = db.neighbor(&s, false).await?;
    if previous.is_some() {
        db.neighbor(&s, false).await?;
    }
    let next = db.neighbor(&s, true).await?;
    if next.is_some() {
        db.neighbor(&s, true).await?;
    }
    let bottom_previous = db.neighbor(&s, false).await?;
    if bottom_previous.is_some() {
        db.neighbor(&s, false).await?;
    }
    let bottom_next = db.neighbor(&s, true).await?;
    if bottom_next.is_some() {
        db.neighbor(&s, true).await?;
    }
    let id = s.id;
    let html = ShelterTemplate {
        asset_version: asset_cache::VERSION,
        shelter: s,
        days,
        previous,
        next,
        updated,
        freeze,
        analytics: cfg.analytics,
    }
    .render()
    .map_err(|_| Error::Render)?;
    if cfg.cache && write_page_cache {
        write_cache(cfg, &format!("shelters/{id}.html"), html.as_bytes()).await;
    }
    Ok(Html(html).into_response())
}
async fn read_home(cfg: &Config) -> Option<String> {
    let path = cfg.cache_dir.join("index.html");
    let metadata = tokio::fs::metadata(&path).await.ok()?;
    if metadata.len() > 8 * 1024 * 1024 {
        return None;
    }
    let age = SystemTime::now()
        .duration_since(metadata.modified().ok()?)
        .ok()?;
    // Original condition truncates seconds first, then divides by 3600.0 and checks > 2.
    if age.as_secs() > 7200 {
        return None;
    }
    tokio::fs::read_to_string(path).await.ok()
}
async fn write_cache(cfg: &Config, name: &str, bytes: &[u8]) {
    // Oversized results still render completely; only the optional file-cache write is skipped.
    if bytes.len() > 8 * 1024 * 1024 {
        return;
    }
    let path = cfg.cache_dir.join(name);
    if let Some(parent) = path.parent()
        && (tokio::fs::create_dir_all(parent).await.is_err()
            || tokio::fs::write(path, bytes).await.is_err())
    {
        tracing::warn!("page cache write failed");
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ruby_distance_conversion() {
        assert_eq!(ruby_float("10.5 miles"), 10.5);
        assert_eq!(ruby_float("oops"), 0.0);
        assert_eq!(ruby_float("  -3"), -3.0);
        assert!(finite("NaN").is_err());
    }
}
