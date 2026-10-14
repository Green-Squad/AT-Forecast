//! Destination-only OpenWeather updater. Readers keep the previous complete generation
//! until all shelters, forecasts, copied tables and foreign keys have been validated.
use chrono::{Duration as ChronoDuration, SecondsFormat, Utc};
use libsql::{Builder, Connection as RawConnection, TransactionBehavior, Value};
use serde_json::{Value as Json, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    time::{Duration, Instant},
};

pub type Result<T> = std::result::Result<T, Failure>;
#[derive(Debug)]
pub struct Failure(pub &'static str);
impl From<libsql::Error> for Failure {
    fn from(error: libsql::Error) -> Self {
        #[cfg(debug_assertions)]
        if env::var("TURSO_TEST_DATABASE").as_deref() == Ok("isolated")
            && env::var("WEATHER_TEST_URL").is_ok()
        {
            eprintln!("isolated weather fixture database error: {error:?}");
        }
        #[cfg(not(debug_assertions))]
        let _ = error;
        Self("database")
    }
}
impl From<reqwest::Error> for Failure {
    fn from(_: reqwest::Error) -> Self {
        Self("provider")
    }
}
struct Connection(RawConnection);
impl Connection {
    async fn execute(&self, sql: &str, args: impl libsql::params::IntoParams) -> Result<u64> {
        tokio::time::timeout(Duration::from_secs(20), self.0.execute(sql, args))
            .await
            .map_err(|_| Failure("database timeout"))?
            .map_err(Into::into)
    }
    async fn query(
        &self,
        sql: &str,
        args: impl libsql::params::IntoParams,
    ) -> Result<libsql::Rows> {
        tokio::time::timeout(Duration::from_secs(20), self.0.query(sql, args))
            .await
            .map_err(|_| Failure("database timeout"))?
            .map_err(Into::into)
    }
    async fn transaction_with_behavior(
        &self,
        behavior: TransactionBehavior,
    ) -> Result<libsql::Transaction> {
        tokio::time::timeout(
            Duration::from_secs(20),
            self.0.transaction_with_behavior(behavior),
        )
        .await
        .map_err(|_| Failure("database timeout"))?
        .map_err(Into::into)
    }
}

fn utc() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Micros, false)
}
fn ident(value: &str) -> Result<String> {
    if value.is_empty() || !value.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err(Failure("schema"));
    }
    Ok(format!("\"{value}\""))
}
async fn scalar(c: &Connection, sql: &str, args: Vec<Value>) -> Result<i64> {
    c.query(sql, args)
        .await?
        .next()
        .await?
        .ok_or(Failure("missing metadata"))?
        .get(0)
        .map_err(Into::into)
}
fn field<'a>(v: &'a Json, name: &str) -> Result<&'a Json> {
    v.get(name).ok_or(Failure("forecast shape"))
}
fn number(v: &Json) -> Result<f64> {
    v.as_f64()
        .filter(|n| n.is_finite())
        .ok_or(Failure("forecast number"))
}
fn fahrenheit(v: &Json) -> Result<i64> {
    let k = number(v)?;
    if !(150.0..=350.0).contains(&k) {
        return Err(Failure("temperature range"));
    }
    // Rails writes floats into integer columns via ActiveRecord's to_i, not rounding.
    Ok((k * 9.0 / 5.0 - 459.67).trunc() as i64)
}
fn wind(speed: &Json, degree: &Json) -> Result<String> {
    let speed = number(speed)?;
    let degree_number = number(degree)?;
    if !(0.0..=150.0).contains(&speed) || !(0.0..=360.0).contains(&degree_number) {
        return Err(Failure("wind range"));
    }
    // Ruby divides integer JSON degrees using integer division, float degrees as floats.
    let sectors = if degree.is_i64() || degree.is_u64() {
        (degree_number / 45.0).trunc()
    } else {
        degree_number / 45.0
    };
    let direction =
        ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][((sectors + 0.5).trunc() as usize) % 8];
    Ok(format!(
        "{} mph {direction}",
        (speed / 0.44704).round() as i64
    ))
}
fn description(v: &Json) -> Result<String> {
    let s = v
        .get("weather")
        .and_then(Json::as_array)
        .and_then(|a| a.first())
        .and_then(|x| x.get("description"))
        .and_then(Json::as_str)
        .ok_or(Failure("forecast description"))?;
    if s.is_empty() || s.len() > 512 {
        return Err(Failure("forecast description"));
    }
    Ok(s.into())
}
#[derive(Clone, Debug)]
pub struct Forecast {
    pub time: i64,
    pub high: i64,
    pub low: i64,
    pub description: String,
    pub wind: String,
}
pub fn parse_forecast(value: &Json, daily: bool, now: i64) -> Result<Vec<Forecast>> {
    let items = value
        .get("list")
        .and_then(Json::as_array)
        .ok_or(Failure("forecast list"))?;
    let minimum = if daily { 7 } else { 40 };
    if items.len() != minimum {
        return Err(Failure("incomplete forecast"));
    }
    let mut times = BTreeSet::new();
    let mut result = Vec::new();
    for item in items {
        let time = field(item, "dt")?
            .as_i64()
            .ok_or(Failure("forecast time"))?;
        if !(now - 172800..=now + 16 * 86400).contains(&time) || !times.insert(time) {
            return Err(Failure("forecast time"));
        }
        let (high, low, w) = if daily {
            let temp = field(item, "temp")?;
            (
                fahrenheit(field(temp, "max")?)?,
                fahrenheit(field(temp, "min")?)?,
                wind(field(item, "speed")?, field(item, "deg")?)?,
            )
        } else {
            let temp = fahrenheit(field(field(item, "main")?, "temp")?)?;
            let w = field(item, "wind")?;
            (temp, temp, wind(field(w, "speed")?, field(w, "deg")?)?)
        };
        if daily && low > high {
            return Err(Failure("forecast temperature order"));
        }
        result.push(Forecast {
            time,
            high,
            low,
            description: description(item)?,
            wind: w,
        });
    }
    result.sort_by_key(|r| r.time);
    Ok(result)
}

struct Provider {
    client: reqwest::Client,
    base: String,
    key: String,
    interval: Duration,
    last: Option<Instant>,
    calls: usize,
}
impl Provider {
    async fn fetch(&mut self, lat: &str, lon: &str, daily: bool) -> Result<Vec<Forecast>> {
        let endpoint = if daily { "forecast/daily" } else { "forecast" };
        for attempt in 0..3 {
            if let Some(last) = self.last {
                tokio::time::sleep(self.interval.saturating_sub(last.elapsed())).await;
            }
            self.last = Some(Instant::now());
            self.calls += 1;
            let response = self
                .client
                .get(format!("{}/data/2.5/{endpoint}", self.base))
                .query(&[
                    ("lat", lat),
                    ("lon", lon),
                    ("appid", &self.key),
                    ("cnt", if daily { "7" } else { "40" }),
                ])
                .send()
                .await;
            match response {
                Ok(r) if r.status().is_success() => {
                    return parse_forecast(&r.json::<Json>().await?, daily, Utc::now().timestamp());
                }
                Ok(r) if r.status().as_u16() == 429 => return Err(Failure("provider rate limit")),
                Ok(r) if !r.status().is_server_error() => return Err(Failure("provider access")),
                _ if attempt < 2 => {
                    tokio::time::sleep(Duration::from_secs(2u64.pow(attempt + 1))).await
                }
                _ => return Err(Failure("provider unavailable")),
            }
        }
        Err(Failure("provider unavailable"))
    }
}
async fn renew(c: &Connection, owner: &str) -> Result<()> {
    let expires =
        (Utc::now() + ChronoDuration::minutes(30)).to_rfc3339_opts(SecondsFormat::Micros, false);
    if c.execute(
        "UPDATE _sync_lease SET expires_utc=? WHERE id=1 AND owner=?",
        (expires, owner),
    )
    .await?
        != 1
    {
        return Err(Failure("lost lease"));
    }
    Ok(())
}
async fn insert_forecasts(
    c: &Connection,
    generation: i64,
    table: &str,
    shelter: i64,
    rows: &[Forecast],
    next: &mut i64,
    created: &str,
) -> Result<()> {
    let daily = table == "weathers";
    let columns = if daily {
        "id,weather_date,high,low,description,shelter_id,created_at,updated_at,wind"
    } else {
        "id,date,temp,description,shelter_id,created_at,updated_at,wind"
    };
    for chunk in rows.chunks(50) {
        let mut args = Vec::new();
        let mut groups = Vec::new();
        for r in chunk {
            if *next >= 9_007_199_254_740_991 {
                return Err(Failure("ID exhaustion"));
            }
            let date = chrono::DateTime::from_timestamp(r.time, 0)
                .ok_or(Failure("forecast time"))?
                .format("%Y-%m-%dT%H:%M:%S%.6f")
                .to_string();
            args.extend([
                Value::Integer(*next),
                Value::Text(date),
                Value::Integer(r.high),
            ]);
            *next += 1;
            if daily {
                args.push(Value::Integer(r.low));
            }
            args.extend([
                Value::Text(r.description.clone()),
                Value::Integer(shelter),
                Value::Text(created.into()),
                Value::Text(created.into()),
                Value::Text(r.wind.clone()),
            ]);
            groups.push(format!(
                "({})",
                vec!["?"; if daily { 9 } else { 8 }].join(",")
            ));
        }
        c.execute(
            &format!(
                "INSERT INTO {} ({columns}) VALUES {}",
                ident(&format!("_g{generation}_{table}"))?,
                groups.join(",")
            ),
            args,
        )
        .await?;
    }
    Ok(())
}
async fn update(c: &Connection, provider: &mut Provider, owner: &str) -> Result<Json> {
    let started = Instant::now();
    let snapshot = utc();
    let mut state = c
        .query(
            "SELECT generation,schema_sha256 FROM _migration_state WHERE id=1",
            (),
        )
        .await?;
    let state = state.next().await?.ok_or(Failure("migration state"))?;
    let old: i64 = state.get(0)?;
    let fingerprint: String = state.get(1)?;
    let generation = old.checked_add(1).ok_or(Failure("generation"))?;
    if old < 1 {
        return Err(Failure("generation"));
    }
    let mut tables = Vec::new();
    let mut names = c
        .query(
            "SELECT table_name FROM _source_fingerprints ORDER BY table_name",
            (),
        )
        .await?;
    while let Some(r) = names.next().await? {
        let name: String = r.get(0)?;
        ident(&name)?;
        tables.push(name);
    }
    if !["shelters", "weathers", "hourly_weathers"]
        .iter()
        .all(|n| tables.iter().any(|x| x == n))
    {
        return Err(Failure("application schema"));
    }
    c.execute("CREATE TABLE IF NOT EXISTS _rust_weather_state(id INTEGER PRIMARY KEY CHECK(id=1),owns_forecasts INTEGER NOT NULL DEFAULT 0,generation INTEGER NOT NULL DEFAULT 0,next_id INTEGER NOT NULL DEFAULT 4294967296,refreshed_utc TEXT NOT NULL DEFAULT '')",()).await?;
    c.execute(
        "INSERT INTO _rust_weather_state(id) VALUES(1) ON CONFLICT DO NOTHING",
        (),
    )
    .await?;
    let mut next = scalar(
        c,
        "SELECT next_id FROM _rust_weather_state WHERE id=1",
        vec![],
    )
    .await?;
    let mut shelters = Vec::new();
    let mut rows = c
        .query(
            &format!(
                "SELECT id,latt,long FROM {} ORDER BY id",
                ident(&format!("_g{old}_shelters"))?
            ),
            (),
        )
        .await?;
    while let Some(row) = rows.next().await? {
        let id: i64 = row.get(0)?;
        let lat: String = row.get(1)?;
        let lon: String = row.get(2)?;
        let latitude = lat
            .parse::<f64>()
            .map_err(|_| Failure("shelter coordinates"))?;
        let longitude = lon
            .parse::<f64>()
            .map_err(|_| Failure("shelter coordinates"))?;
        if !latitude.is_finite()
            || !longitude.is_finite()
            || !(-90.0..=90.0).contains(&latitude)
            || !(-180.0..=180.0).contains(&longitude)
        {
            return Err(Failure("shelter coordinates"));
        }
        shelters.push((id, lat, lon));
    }
    if shelters.is_empty() {
        return Err(Failure("no shelters"));
    }
    let mut dependencies = BTreeMap::new();
    for table in &tables {
        let mut rows = c
            .query(
                &format!(
                    "PRAGMA foreign_key_list({})",
                    ident(&format!("_g{old}_{table}"))?
                ),
                (),
            )
            .await?;
        let mut parents = BTreeSet::new();
        while let Some(row) = rows.next().await? {
            let parent: String = row.get(2)?;
            parents.insert(
                parent
                    .strip_prefix(&format!("_g{old}_"))
                    .ok_or(Failure("foreign schema"))?
                    .to_owned(),
            );
        }
        dependencies.insert(table.clone(), parents);
    }
    let mut ordered = Vec::new();
    let mut done = BTreeSet::new();
    while ordered.len() < tables.len() {
        let ready: Vec<_> = tables
            .iter()
            .filter(|t| !done.contains(*t) && dependencies[*t].iter().all(|p| done.contains(p)))
            .cloned()
            .collect();
        if ready.is_empty() {
            return Err(Failure("cyclic schema"));
        }
        for table in ready {
            done.insert(table.clone());
            ordered.push(table);
        }
    }
    // Reuse only a staging generation we explicitly own. Never replace unrelated tables.
    c.execute("CREATE TABLE IF NOT EXISTS _weather_staging(generation INTEGER PRIMARY KEY,owner TEXT NOT NULL)",()).await?;
    let mut existing = c
        .query(
            "SELECT owner FROM _weather_staging WHERE generation=?",
            [generation],
        )
        .await?;
    let owned = existing.next().await?.is_some();
    for table in ordered.iter().rev() {
        let name = format!("_g{generation}_{table}");
        let exists = scalar(
            c,
            "SELECT COUNT(*) FROM sqlite_master WHERE name=?",
            vec![Value::Text(name.clone())],
        )
        .await?;
        if exists > 0 && !owned {
            return Err(Failure("unowned staging generation"));
        }
        if owned {
            c.execute(&format!("DROP TABLE IF EXISTS {}", ident(&name)?), ())
                .await?;
        }
    }
    c.execute("INSERT INTO _weather_staging(generation,owner) VALUES(?,?) ON CONFLICT(generation) DO UPDATE SET owner=excluded.owner",(generation,owner)).await?;
    let mut counts = BTreeMap::new();
    for table in &tables {
        let previous = format!("_g{old}_{table}");
        let mut ddl = c
            .query(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                [previous.clone()],
            )
            .await?;
        let sql: String = ddl.next().await?.ok_or(Failure("table schema"))?.get(0)?;
        c.execute(
            &sql.replace(&format!("_g{old}_"), &format!("_g{generation}_")),
            (),
        )
        .await?;
    }
    for table in &ordered {
        let previous = format!("_g{old}_{table}");
        let target = format!("_g{generation}_{table}");
        if table != "weathers" && table != "hourly_weathers" {
            c.execute(
                &format!(
                    "INSERT INTO {} SELECT * FROM {}",
                    ident(&target)?,
                    ident(&previous)?
                ),
                (),
            )
            .await?;
            counts.insert(
                table.clone(),
                scalar(
                    c,
                    &format!("SELECT COUNT(*) FROM {}", ident(&previous)?),
                    vec![],
                )
                .await?,
            );
        }
    }
    let mut daily_count = 0;
    let mut hourly_count = 0;
    for (index, (id, lat, lon)) in shelters.iter().enumerate() {
        renew(c, owner).await?;
        let daily = provider.fetch(lat, lon, true).await?;
        let daily_created = Utc::now().format("%Y-%m-%dT%H:%M:%S%.6f").to_string();
        let hourly = provider.fetch(lat, lon, false).await?;
        let hourly_created = Utc::now().format("%Y-%m-%dT%H:%M:%S%.6f").to_string();
        insert_forecasts(
            c,
            generation,
            "weathers",
            *id,
            &daily,
            &mut next,
            &daily_created,
        )
        .await?;
        insert_forecasts(
            c,
            generation,
            "hourly_weathers",
            *id,
            &hourly,
            &mut next,
            &hourly_created,
        )
        .await?;
        daily_count += daily.len();
        hourly_count += hourly.len();
        if (index + 1) % 25 == 0 {
            tracing::info!(
                shelters_processed = index + 1,
                shelters_total = shelters.len(),
                provider_calls = provider.calls,
                duration_seconds = started.elapsed().as_secs(),
                "weather fetch progress"
            );
        }
    }
    counts.insert("weathers".into(), daily_count as i64);
    counts.insert("hourly_weathers".into(), hourly_count as i64);
    for table in &tables {
        let previous = format!("_g{old}_{table}");
        let target = format!("_g{generation}_{table}");
        let mut indexes=c.query("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",[previous]).await?;
        while let Some(row) = indexes.next().await? {
            let sql: String = row.get(0)?;
            c.execute(
                &sql.replace(&format!("_g{old}_"), &format!("_g{generation}_")),
                (),
            )
            .await?;
        }
        if scalar(
            c,
            &format!("SELECT COUNT(*) FROM {}", ident(&target)?),
            vec![],
        )
        .await?
            != counts[table]
        {
            return Err(Failure("row validation"));
        }
        if c.query(
            &format!("PRAGMA foreign_key_check({})", ident(&target)?),
            (),
        )
        .await?
        .next()
        .await?
        .is_some()
        {
            return Err(Failure("relationships"));
        }
    }
    let mut integrity = c.query("PRAGMA integrity_check", ()).await?;
    if integrity
        .next()
        .await?
        .ok_or(Failure("integrity"))?
        .get::<String>(0)?
        != "ok"
    {
        return Err(Failure("integrity"));
    }
    renew(c, owner).await?;
    let published = utc();
    let report = json!({"run_id":owner,"method":"openweather-validated-generation","status":"validated","generation":generation,"source_snapshot_utc":snapshot,"published_utc":published,"shelters":shelters.len(),"daily_records":daily_count,"hourly_records":hourly_count,"provider_calls":provider.calls,"table_counts":counts,"duration_seconds":started.elapsed().as_secs_f64()});
    let tx = c
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .await?;
    let mut guard = tx
        .query("SELECT generation FROM _migration_state WHERE id=1", ())
        .await?;
    if guard
        .next()
        .await?
        .ok_or(Failure("migration state"))?
        .get::<i64>(0)?
        != old
    {
        return Err(Failure("concurrent publication"));
    }
    tx.execute(
        "UPDATE _migration_state SET generation=?,source_snapshot_utc=?,published_utc=? WHERE id=1",
        (generation, snapshot, published.clone()),
    )
    .await?;
    tx.execute("UPDATE _rust_weather_state SET owns_forecasts=1,generation=?,next_id=?,refreshed_utc=? WHERE id=1",(generation,next,published)).await?;
    tx.execute(
        "INSERT INTO _migration_reports(generation,report) VALUES(?,?)",
        (generation, report.to_string()),
    )
    .await?;
    tx.execute(
        "INSERT INTO _sync_runs(run_id,report) VALUES(?,?)",
        (owner, report.to_string()),
    )
    .await?;
    tx.execute(
        "DELETE FROM _weather_staging WHERE generation=?",
        [generation],
    )
    .await?;
    tx.commit().await?;
    // Keep the current and immediately previous complete generations for recovery.
    let mut catalog = c
        .query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB '_g[0-9]*_*'",
            (),
        )
        .await?;
    let mut obsolete = Vec::new();
    while let Some(row) = catalog.next().await? {
        let name: String = row.get(0)?;
        let number = name
            .strip_prefix("_g")
            .and_then(|x| x.split('_').next())
            .and_then(|x| x.parse::<i64>().ok());
        if number.is_some_and(|n| n < generation - 1) {
            ident(&name)?;
            obsolete.push(name);
        }
    }
    obsolete.sort_by_key(|name| {
        let table = name
            .strip_prefix("_g")
            .and_then(|v| v.split_once('_'))
            .map(|(_, t)| t)
            .unwrap_or("");
        std::cmp::Reverse(ordered.iter().position(|t| t == table).unwrap_or(0))
    });
    for name in obsolete {
        c.execute(&format!("DROP TABLE {}", ident(&name)?), ())
            .await?;
    }
    // Report retention is bounded separately from forecast generations.
    c.execute(
        "DELETE FROM _migration_reports WHERE generation<?",
        [generation - 1],
    )
    .await?;
    c.execute("DELETE FROM _sync_runs WHERE run_id NOT IN (SELECT run_id FROM _sync_runs ORDER BY rowid DESC LIMIT 2000)",()).await?;
    let _ = fingerprint;
    Ok(report)
}
pub struct Settings {
    pub url: String,
    pub token: String,
    pub key: String,
    pub base: String,
    pub interval_ms: u64,
}
pub async fn run_from_env() -> Result<Json> {
    run(Settings {
        url: env::var("TURSO_DATABASE_URL").map_err(|_| Failure("configuration"))?,
        token: env::var("TURSO_AUTH_TOKEN").map_err(|_| Failure("configuration"))?,
        key: env::var("OPEN_WEATHER_MAP_API_KEY").map_err(|_| Failure("configuration"))?,
        base: env::var("WEATHER_API_ORIGIN")
            .unwrap_or_else(|_| "https://api.openweathermap.org".into()),
        interval_ms: 2100,
    })
    .await
}
pub async fn run(settings: Settings) -> Result<Json> {
    let Settings {
        url,
        token,
        key,
        base,
        interval_ms,
    } = settings;
    let isolated = env::var("TURSO_TEST_DATABASE").as_deref() == Ok("isolated");
    let local = |s: &str| {
        reqwest::Url::parse(s).ok().is_some_and(|u| {
            u.scheme() == "http"
                && matches!(
                    u.host_str(),
                    Some(
                        "localhost"
                            | "127.0.0.1"
                            | "fixture-weather"
                            | "at-fixture-libsql"
                            | "fixture-sync"
                    )
                )
        })
    };
    if (!url.starts_with("libsql://") && !url.starts_with("https://") && !(isolated && local(&url)))
        || (base != "https://api.openweathermap.org" && !(isolated && local(&base)))
        || key.trim().is_empty()
    {
        return Err(Failure("configuration"));
    }
    if !isolated && interval_ms < 2100 {
        return Err(Failure("provider rate configuration"));
    }
    let database = tokio::time::timeout(
        Duration::from_secs(15),
        Builder::new_remote(url, token.trim().to_owned()).build(),
    )
    .await
    .map_err(|_| Failure("database timeout"))??;
    let c = Connection(database.connect()?);
    // Staging is private; validation checks every FK before the atomic pointer flip.
    c.execute("PRAGMA foreign_keys=OFF", ()).await?;
    let owner = format!(
        "weather-{}-{}",
        Utc::now().timestamp_nanos_opt().ok_or(Failure("clock"))?,
        std::process::id()
    );
    let expires =
        (Utc::now() + ChronoDuration::minutes(30)).to_rfc3339_opts(SecondsFormat::Micros, false);
    let acquired=c.execute("INSERT INTO _sync_lease(id,owner,expires_utc) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires_utc=excluded.expires_utc WHERE _sync_lease.expires_utc<?",(owner.clone(),expires,utc())).await?;
    if acquired != 1 {
        return Ok(json!({"status":"skipped-overlap","method":"openweather"}));
    }
    let mut provider = Provider {
        client: reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(20))
            .build()?,
        base,
        key,
        interval: Duration::from_millis(interval_ms),
        last: None,
        calls: 0,
    };
    let result =
        match tokio::time::timeout(Duration::from_secs(3300), update(&c, &mut provider, &owner))
            .await
        {
            Ok(r) => r,
            Err(_) => Err(Failure("job deadline")),
        };
    let _ = c
        .execute("DELETE FROM _sync_lease WHERE id=1 AND owner=?", [owner])
        .await;
    result
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rails_conversions() {
        assert_eq!(fahrenheit(&json!(273.15)).unwrap(), 31);
        assert_eq!(fahrenheit(&json!(270.0)).unwrap(), 26);
        assert_eq!(wind(&json!(0.44704), &json!(23)).unwrap(), "1 mph N");
        assert_eq!(wind(&json!(0.44704), &json!(23.0)).unwrap(), "1 mph NE");
        assert!(fahrenheit(&json!(900.0)).is_err());
    }
    #[test]
    fn incomplete_and_duplicate_forecasts_fail() {
        assert!(parse_forecast(&json!({"list":[]}), true, 0).is_err());
        let item = json!({"dt":0,"temp":{"max":280,"min":270},"weather":[{"description":"clear"}],"speed":1,"deg":0});
        assert!(parse_forecast(&json!({"list":vec![item;7]}), true, 0).is_err());
    }
}
