use at_forecast::weather::{Settings, run};
use axum::{
    Json, Router,
    extract::{OriginalUri, State},
    http::StatusCode,
    routing::get,
};
use libsql::Builder;
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
#[derive(Default)]
struct Mock {
    mode: AtomicUsize,
    retry: AtomicUsize,
    delay_ms: AtomicUsize,
    active: AtomicUsize,
    max_active: AtomicUsize,
}
struct Active<'a>(&'a AtomicUsize);
impl Drop for Active<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}
async fn forecast(
    State(state): State<Arc<Mock>>,
    OriginalUri(uri): OriginalUri,
) -> (StatusCode, Json<Value>) {
    let active = state.active.fetch_add(1, Ordering::SeqCst) + 1;
    let _active = Active(&state.active);
    state.max_active.fetch_max(active, Ordering::SeqCst);
    tokio::time::sleep(std::time::Duration::from_millis(
        state.delay_ms.load(Ordering::SeqCst) as u64,
    ))
    .await;
    if state.mode.load(Ordering::SeqCst) == 1 {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"fixture"})),
        );
    }
    if state.mode.load(Ordering::SeqCst) == 3 {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Json(json!({"error":"fixture"})),
        );
    }
    if state.retry.swap(0, Ordering::SeqCst) > 0 {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"fixture"})),
        );
    }
    let daily = uri.path().ends_with("daily");
    let count = if state.mode.load(Ordering::SeqCst) == 2 {
        1
    } else if daily {
        7
    } else {
        40
    };
    let now = chrono::Utc::now().timestamp();
    let rows:Vec<_>=(0..count).map(|i|json!({"dt":now+i*if daily{86400}else{10800},"temp":{"max":280,"min":270},"main":{"temp":275},"weather":[{"description":"fixture weather"}],"speed":1,"deg":90,"wind":{"speed":1,"deg":90}})).collect();
    (StatusCode::OK, Json(json!({"list":rows})))
}
#[tokio::test]
async fn provider_publication_retry_failure_and_overlap() {
    let Ok(url) = std::env::var("WEATHER_TEST_URL") else {
        eprintln!("SKIPPED weather database integration: WEATHER_TEST_URL is required");
        return;
    };
    assert_eq!(
        std::env::var("TURSO_TEST_DATABASE").as_deref(),
        Ok("isolated")
    );
    let parsed = reqwest::Url::parse(&url).unwrap();
    assert_eq!(parsed.scheme(), "http");
    assert!(matches!(
        parsed.host_str(),
        Some("localhost" | "127.0.0.1" | "fixture-sync")
    ));
    let database = Builder::new_remote(url.clone(), String::new())
        .build()
        .await
        .unwrap();
    let c = database.connect().unwrap();
    let mut names = c
        .query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
            (),
        )
        .await
        .unwrap();
    let mut tables = Vec::new();
    while let Some(row) = names.next().await.unwrap() {
        tables.push(row.get::<String>(0).unwrap());
    }
    for name in tables {
        assert!(name.chars().all(|x| x.is_ascii_alphanumeric() || x == '_'));
        c.execute(&format!("DROP TABLE \"{name}\""), ())
            .await
            .unwrap();
    }
    c.execute_batch("CREATE TABLE _migration_state(id INTEGER PRIMARY KEY,generation INTEGER,schema_sha256 TEXT,source_snapshot_utc TEXT,published_utc TEXT,syncing INTEGER DEFAULT 0); INSERT INTO _migration_state VALUES(1,1,'fixture','fixture','fixture',0); CREATE TABLE _source_fingerprints(table_name TEXT PRIMARY KEY); INSERT INTO _source_fingerprints VALUES('states'),('shelters'),('keys'),('weathers'),('hourly_weathers'); CREATE TABLE _migration_reports(generation INTEGER PRIMARY KEY,report TEXT); CREATE TABLE _sync_runs(run_id TEXT PRIMARY KEY,report TEXT); CREATE TABLE _sync_lease(id INTEGER PRIMARY KEY,owner TEXT,expires_utc TEXT); CREATE TABLE _g1_states(id INTEGER PRIMARY KEY,name TEXT); INSERT INTO _g1_states VALUES(1,'fixture'); CREATE TABLE _g1_shelters(id INTEGER PRIMARY KEY,latt TEXT,long TEXT,state_id INTEGER REFERENCES _g1_states(id)); INSERT INTO _g1_shelters VALUES(1,'35.1','-81.1',1),(2,'35.2','-81.2',1); CREATE INDEX _g1_shelter_state ON _g1_shelters(state_id); CREATE TABLE _g1_keys(id INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT); INSERT INTO _g1_keys VALUES(1,'fixture-private'); UPDATE sqlite_sequence SET seq=42 WHERE name='_g1_keys'; CREATE TABLE _g1_weathers(id INTEGER PRIMARY KEY,weather_date TEXT,high INTEGER,low INTEGER,description TEXT,shelter_id INTEGER REFERENCES _g1_shelters(id),created_at TEXT,updated_at TEXT,wind TEXT); CREATE TABLE _g1_hourly_weathers(id INTEGER PRIMARY KEY,date TEXT,temp INTEGER,description TEXT,shelter_id INTEGER REFERENCES _g1_shelters(id),created_at TEXT,updated_at TEXT,wind TEXT);").await.unwrap();
    let mock = Arc::new(Mock::default());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/data/2.5/forecast", get(forecast))
        .route("/data/2.5/forecast/daily", get(forecast))
        .with_state(mock.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let settings = || Settings {
        url: url.clone(),
        token: String::new(),
        key: "fixture-key".into(),
        base: base.clone(),
        interval_ms: 0,
    };
    mock.delay_ms.store(100, Ordering::SeqCst);
    let initial = run(settings()).await.unwrap();
    assert_eq!(
        mock.max_active.load(Ordering::SeqCst),
        4,
        "Slow responses should overlap, with no more than four in flight"
    );
    mock.delay_ms.store(0, Ordering::SeqCst);
    assert_eq!(initial["generation"], 2);
    assert_eq!(initial["daily_records"], 14);
    assert_eq!(initial["hourly_records"], 80);
    let mut sequence = c
        .query("SELECT seq FROM sqlite_sequence WHERE name='_g2_keys'", ())
        .await
        .unwrap();
    assert_eq!(
        sequence
            .next()
            .await
            .unwrap()
            .unwrap()
            .get::<i64>(0)
            .unwrap(),
        42
    );

    let count = |table: &str| format!("SELECT COUNT(*) FROM {table}");
    let mut rows = c.query(&count("_g2_weathers"), ()).await.unwrap();
    assert_eq!(
        rows.next().await.unwrap().unwrap().get::<i64>(0).unwrap(),
        14
    );
    for mode in [1, 2, 3] {
        mock.mode.store(mode, Ordering::SeqCst);
        assert!(run(settings()).await.is_err());
        let mut rows = c
            .query("SELECT generation FROM _migration_state", ())
            .await
            .unwrap();
        assert_eq!(
            rows.next().await.unwrap().unwrap().get::<i64>(0).unwrap(),
            2
        );
        let mut rows = c.query(&count("_g2_hourly_weathers"), ()).await.unwrap();
        assert_eq!(
            rows.next().await.unwrap().unwrap().get::<i64>(0).unwrap(),
            80
        );
    }
    mock.mode.store(0, Ordering::SeqCst);
    mock.retry.store(1, Ordering::SeqCst);
    let retry = run(settings()).await.unwrap();
    assert_eq!(retry["generation"], 3);
    assert_eq!(retry["provider_calls"], 5);
    let mut rows = c.query("SELECT key FROM _g3_keys", ()).await.unwrap();
    assert_eq!(
        rows.next()
            .await
            .unwrap()
            .unwrap()
            .get::<String>(0)
            .unwrap(),
        "fixture-private"
    );
    assert!(
        c.query("PRAGMA foreign_key_check", ())
            .await
            .unwrap()
            .next()
            .await
            .unwrap()
            .is_none()
    );
    c.execute(
        "INSERT INTO _sync_lease VALUES(1,'other','2099-01-01T00:00:00.000000+00:00')",
        (),
    )
    .await
    .unwrap();
    assert_eq!(run(settings()).await.unwrap()["status"], "skipped-overlap");
    server.abort();
}
