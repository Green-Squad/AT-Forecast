use crate::{Config, Error};
use bigdecimal::{BigDecimal, ToPrimitive};
use chrono::{NaiveDateTime, TimeZone, Utc};
use chrono_tz::Tz;
use serde_json::{Value, json};
use sqlx::{
    FromRow, PgPool, Postgres,
    pool::PoolConnection,
    postgres::{PgArguments, PgRow},
    query::QueryAs,
};
use std::{
    collections::HashMap,
    time::{Duration, Instant},
};

#[derive(Clone, Debug, FromRow)]
pub struct Shelter {
    pub id: i32,
    pub name: Option<String>,
    pub mileage: BigDecimal,
    pub elevation: Option<i32>,
    pub latt: Option<BigDecimal>,
    pub long: Option<BigDecimal>,
}
impl Shelter {
    pub fn name_label(&self) -> &str {
        self.name.as_deref().unwrap_or("")
    }
    pub fn elevation_text(&self) -> String {
        self.elevation.map(|v| v.to_string()).unwrap_or_default()
    }
    pub fn mileage_text(&self) -> String {
        decimal_text(&self.mileage)
    }
    pub fn southbound(&self) -> f64 {
        (BigDecimal::from(2193) - &self.mileage)
            .to_f64()
            .unwrap_or_default()
    }
    pub fn catalog_json(&self) -> Value {
        json!({"shelter_id":self.id,"name":self.name,"mileage":self.mileage_text(),"elevation":self.elevation,
            "latt":self.latt.as_ref().map(decimal_text),"long":self.long.as_ref().map(decimal_text),"southbound_mileage":self.southbound()})
    }
}
pub fn decimal_text(d: &BigDecimal) -> String {
    let s = d.normalized().to_plain_string();
    if s.contains('.') { s } else { format!("{s}.0") }
}
#[derive(FromRow)]
struct State {
    id: i32,
    name: String,
}
#[derive(FromRow)]
struct Average {
    high: i32,
    low: i32,
}
pub struct StateView {
    pub id: i32,
    pub name: String,
    pub slug: String,
    pub high: i64,
    pub low: i64,
    pub shelters: Vec<Shelter>,
}
#[derive(Debug, FromRow)]
pub struct Daily {
    pub id: i32,
    pub weather_date: NaiveDateTime,
    pub high: i32,
    pub low: i32,
    pub description: String,
    pub wind: Option<String>,
    pub shelter_id: i32,
    pub created_at: NaiveDateTime,
}
#[derive(Debug, FromRow)]
pub struct Hourly {
    pub id: i32,
    pub date: NaiveDateTime,
    pub temp: Option<i32>,
    pub description: Option<String>,
    pub wind: Option<String>,
}
pub struct DayView {
    pub daily: Daily,
    pub hourly: Vec<Hourly>,
    pub date_label: String,
    pub category: &'static str,
    pub times: Vec<String>,
}
impl Hourly {
    pub fn temp_text(&self) -> String {
        self.temp.map(|v| v.to_string()).unwrap_or_default()
    }
    pub fn description_label(&self) -> &str {
        self.description.as_deref().unwrap_or("")
    }
    pub fn wind_label(&self) -> &str {
        self.wind.as_deref().unwrap_or("")
    }
}
impl DayView {
    pub fn json(&self, cfg: &Config) -> Value {
        json!({"daily_weather_id":self.daily.id,"weather_date":iso(self.daily.weather_date,cfg),
            "high":self.daily.high,"low":self.daily.low,"description":self.daily.description,
            "wind":self.daily.wind,"shelter_id":self.daily.shelter_id,
            "hourly_weather":self.hourly.iter().map(|h|json!({"hourly_weather_id":h.id,"date":iso(h.date,cfg),
                "temp":h.temp,"description":h.description,"wind":h.wind})).collect::<Vec<_>>()})
    }
}
fn local_date(n: NaiveDateTime, zone: Tz) -> chrono::DateTime<Tz> {
    // Ambiguous local storage uses the earlier occurrence; non-existent local timestamps are invalid.
    zone.from_local_datetime(&n)
        .earliest()
        .unwrap_or_else(|| Utc.from_utc_datetime(&n).with_timezone(&zone))
}
pub fn displayed(n: NaiveDateTime, cfg: &Config) -> chrono::DateTime<Tz> {
    local_date(n, cfg.database_timezone).with_timezone(&cfg.display_timezone)
}
fn iso(n: NaiveDateTime, cfg: &Config) -> String {
    displayed(n, cfg).to_rfc3339_opts(chrono::SecondsFormat::Millis, false)
}
pub fn category(description: &str) -> &'static str {
    for (css, words) in [
        ("clear", &["clear"][..]),
        ("rain", &["rain", "drizzle"][..]),
        ("snow", &["snow", "sleet"][..]),
        ("clouds", &["clouds"][..]),
        (
            "extreme",
            &[
                "thunderstorm",
                "tornado",
                "hurricane",
                "tropical storm",
                "hail",
                "storm",
            ][..],
        ),
        ("fog", &["fog", "smoke", "haze", "mist"][..]),
    ] {
        if words.iter().any(|w| description.contains(w)) {
            return css;
        }
    }
    "default"
}
#[derive(Default, Clone)]
pub struct Metrics {
    pub queries: u32,
    pub db_ms: f64,
    pub query_cache_hits: u32,
}
pub struct Db {
    pool: PgPool,
    connection: Option<PoolConnection<Postgres>>,
    timeout: Duration,
    pub metrics: Metrics,
    // Rails' request query cache already avoids the identical repeated neighbor SQL in the ERB.
    neighbors: HashMap<(i32, bool), Option<Shelter>>,
}
macro_rules! rows {
    ($db:expr,$query:expr) => {{
        $db.run($query).await?
    }};
}
impl Db {
    pub fn new(pool: PgPool, timeout: Duration) -> Self {
        Self {
            pool,
            connection: None,
            timeout,
            metrics: Metrics::default(),
            neighbors: HashMap::new(),
        }
    }
    async fn run<T>(
        &mut self,
        query: QueryAs<'_, Postgres, T, PgArguments>,
    ) -> Result<Vec<T>, Error>
    where
        T: for<'r> FromRow<'r, PgRow> + Send + Unpin,
    {
        self.metrics.queries += 1;
        let start = Instant::now();
        let result = tokio::time::timeout(self.timeout, async {
            // Keep one connection until this request's Db is dropped. Acquiring it
            // lazily preserves database-free cache hits and static responses.
            if self.connection.is_none() {
                self.connection = Some(self.pool.acquire().await?);
            }
            let connection = self.connection.as_mut().expect("connection acquired");
            query.fetch_all(&mut **connection).await
        })
        .await;
        self.metrics.db_ms += start.elapsed().as_secs_f64() * 1000.0;
        result
            .map_err(|_| Error::Unavailable)?
            .map_err(|_| Error::Unavailable)
    }
    pub async fn auth(&mut self, key: Option<&str>) -> Result<bool, Error> {
        let keys: Vec<(i32,)> = rows!(
            self,
            sqlx::query_as("SELECT id FROM keys WHERE key = $1 LIMIT 1").bind(key)
        );
        Ok(!keys.is_empty())
    }
    pub async fn states(&mut self, include: bool, cfg: &Config) -> Result<Vec<StateView>, Error> {
        let states: Vec<State> = rows!(self, sqlx::query_as("SELECT * FROM states"));
        let now = Utc::now().with_timezone(&cfg.database_timezone);
        let cutoff = now
            .date_naive()
            .and_hms_micro_opt(23, 59, 59, 999999)
            .unwrap();
        let mut out = Vec::new();
        for s in states {
            // Deliberately keep one query and Rust-side integer averaging per state, as Rails does.
            let averages:Vec<Average> = rows!(self,sqlx::query_as("SELECT weathers.id, high, low FROM weathers INNER JOIN shelters ON shelters.id = weathers.shelter_id WHERE shelters.state_id = $1 AND weathers.weather_date <= $2").bind(s.id).bind(cutoff));
            let (mut high, mut low) = (0i64, 0i64);
            for a in &averages {
                high += i64::from(a.high);
                low += i64::from(a.low);
            }
            if !averages.is_empty() {
                high = high.div_euclid(averages.len() as i64);
                low = low.div_euclid(averages.len() as i64);
            }
            out.push(StateView {
                id: s.id,
                slug: s.name.replace(' ', ""),
                name: s.name,
                high,
                low,
                shelters: Vec::new(),
            });
        }
        // Rails builds the states/averages first, then templates evaluate each lazy shelters relation.
        if include {
            for s in &mut out {
                s.shelters = rows!(
                    self,
                    sqlx::query_as(
                        "SELECT * FROM shelters WHERE state_id = $1 ORDER BY mileage ASC"
                    )
                    .bind(s.id)
                );
            }
        }
        Ok(out)
    }
    pub async fn shelter(&mut self, id: i32) -> Result<Shelter, Error> {
        let mut rows: Vec<Shelter> = rows!(
            self,
            sqlx::query_as("SELECT * FROM shelters WHERE id = $1 LIMIT 1").bind(id)
        );
        rows.pop().ok_or(Error::NotFound)
    }
    pub async fn neighbor(&mut self, s: &Shelter, next: bool) -> Result<Option<Shelter>, Error> {
        if let Some(cached) = self.neighbors.get(&(s.id, next)) {
            self.metrics.query_cache_hits += 1;
            return Ok(cached.clone());
        }
        let sql = if next {
            "SELECT * FROM shelters WHERE mileage > $1 ORDER BY mileage ASC LIMIT 1"
        } else {
            "SELECT * FROM shelters WHERE mileage < $1 ORDER BY mileage DESC LIMIT 1"
        };
        let mut rows: Vec<Shelter> = rows!(self, sqlx::query_as(sql).bind(&s.mileage));
        let found = rows.pop();
        self.neighbors.insert((s.id, next), found.clone());
        Ok(found)
    }
    pub async fn nearest_mile(&mut self, mile: BigDecimal) -> Result<Shelter, Error> {
        let mut rows: Vec<Shelter> = rows!(
            self,
            sqlx::query_as("SELECT * FROM shelters ORDER BY ABS(mileage - $1) ASC LIMIT 1")
                .bind(mile)
        );
        rows.pop().ok_or(Error::NotFound)
    }
    pub async fn nearest_coords(&mut self, lat: f64, lon: f64) -> Result<Shelter, Error> {
        let shelters: Vec<Shelter> = rows!(self, sqlx::query_as("SELECT * FROM shelters"));
        let mut best = None;
        let mut smallest = f64::MAX;
        for s in shelters {
            let d = haversine(
                lat,
                lon,
                s.latt.as_ref().and_then(|v| v.to_f64()).unwrap_or_default(),
                s.long.as_ref().and_then(|v| v.to_f64()).unwrap_or_default(),
            );
            if d < smallest {
                smallest = d;
                best = Some(s);
            }
        }
        best.ok_or(Error::NotFound)
    }
    pub async fn ahead(
        &mut self,
        s: &Shelter,
        distance: BigDecimal,
    ) -> Result<Vec<Shelter>, Error> {
        // No ORDER BY or LIMIT: preserve the original distance endpoint's complete, unordered result.
        Ok(rows!(
            self,
            sqlx::query_as("SELECT * FROM shelters WHERE mileage <= $1 AND mileage > $2")
                .bind(&s.mileage + distance)
                .bind(&s.mileage)
        ))
    }
    pub async fn days(
        &mut self,
        s: &Shelter,
        html: bool,
        cfg: &Config,
    ) -> Result<Vec<DayView>, Error> {
        let daily: Vec<Daily> = rows!(
            self,
            sqlx::query_as(
                "SELECT * FROM weathers WHERE shelter_id = $1 ORDER BY weather_date ASC"
            )
            .bind(s.id)
        );
        let mut out = Vec::new();
        for d in daily {
            let shown = displayed(d.weather_date, cfg);
            let start = shown.date_naive().and_hms_opt(0, 0, 0).unwrap();
            let end = shown
                .date_naive()
                .and_hms_micro_opt(23, 59, 59, 999999)
                .unwrap();
            let start = local_date(start, cfg.display_timezone)
                .with_timezone(&cfg.database_timezone)
                .naive_local();
            let end = local_date(end, cfg.display_timezone)
                .with_timezone(&cfg.database_timezone)
                .naive_local();
            let empty = if html {
                // ActiveRecord Relation#empty? issues SELECT 1 ... LIMIT 1 before the view iterates.
                let exists:Vec<(i32,)>=rows!(self,sqlx::query_as("SELECT 1 FROM hourly_weathers WHERE shelter_id = $1 AND date BETWEEN $2 AND $3 LIMIT 1").bind(s.id).bind(start).bind(end));
                exists.is_empty()
            } else {
                false
            };
            let hourly: Vec<Hourly> = if empty {
                Vec::new()
            } else {
                rows!(self,sqlx::query_as("SELECT * FROM hourly_weathers WHERE shelter_id = $1 AND date BETWEEN $2 AND $3 ORDER BY date ASC").bind(s.id).bind(start).bind(end))
            };
            let times = hourly
                .iter()
                .map(|h| displayed(h.date, cfg).format("%l:%M %p").to_string())
                .collect();
            out.push(DayView {
                date_label: shown.format("%A %B %-d, %Y").to_string(),
                category: category(&d.description),
                daily: d,
                hourly,
                times,
            });
        }
        Ok(out)
    }
}
pub fn haversine(a: f64, b: f64, c: f64, d: f64) -> f64 {
    let (a, b, c, d) = (
        a.to_radians(),
        b.to_radians(),
        c.to_radians(),
        d.to_radians(),
    );
    2.0 * 6371.0
        * (((c - a) / 2.0).sin().powi(2) + a.cos() * c.cos() * ((d - b) / 2.0).sin().powi(2))
            .sqrt()
            .asin()
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn decimals_and_weather_categories_match_rails() {
        assert_eq!(decimal_text(&"630.000".parse().unwrap()), "630.0");
        assert_eq!(decimal_text(&"-81.23400".parse().unwrap()), "-81.234");
        assert_eq!(category("rain and snow"), "rain");
        assert_eq!(category("thunderstorm"), "extreme");
        assert_eq!(category("scattered clouds"), "clouds");
        assert_eq!((-3i64).div_euclid(2), -2);
        assert_eq!(haversine(40.0, -75.0, 40.0, -75.0), 0.0);
    }
    #[tokio::test]
    async fn request_reuses_connection_and_returns_it_on_drop() {
        if std::env::var("AT_FORECAST_TEST_DATABASE").as_deref() != Ok("isolated") {
            eprintln!("SKIPPED PostgreSQL connection lifecycle test: isolated fixture required");
            return;
        }
        let cfg = Config::from_env().unwrap();
        let configured = crate::pool_from_env(&cfg).await.unwrap();
        let pool = sqlx::postgres::PgPoolOptions::new()
            .max_connections(1)
            .connect_lazy_with((*configured.connect_options()).clone());
        configured.close().await;
        let mut db = Db::new(pool.clone(), cfg.query_timeout);
        assert_eq!(pool.size(), 0);
        assert_eq!(db.shelter(1).await.unwrap().id, 1);
        assert_eq!(pool.size(), 1);
        assert!(pool.try_acquire().is_none());
        // A second checkout would time out with the sole connection held.
        assert_eq!(db.shelter(2).await.unwrap().id, 2);
        assert!(pool.try_acquire().is_none());
        drop(db);
        let returned = tokio::time::timeout(Duration::from_secs(1), pool.acquire())
            .await
            .unwrap()
            .unwrap();
        drop(returned);
        pool.close().await;
    }
}
