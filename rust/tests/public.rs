use at_forecast::{App, Config, pool_from_env, router};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use serde_json::Value;
use tower::ServiceExt;

async fn request(app: &App, path: &str) -> (StatusCode, axum::http::HeaderMap, String) {
    let response = router(app.clone())
        .oneshot(Request::builder().uri(path).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let body = response.into_body().collect().await.unwrap().to_bytes();
    (status, headers, String::from_utf8(body.to_vec()).unwrap())
}
fn count(headers: &axum::http::HeaderMap) -> u32 {
    headers["x-db-query-count"]
        .to_str()
        .unwrap()
        .parse()
        .unwrap()
}
#[tokio::test]
async fn public_routes_preserve_responses_with_bounded_queries() {
    // Explicitly opt in; tests never connect to a production DATABASE_URL accidentally.
    if std::env::var("AT_FORECAST_TEST_DATABASE").as_deref() != Ok("isolated") {
        eprintln!(
            "SKIPPED PostgreSQL integration: set AT_FORECAST_TEST_DATABASE=isolated after loading fixture.sql"
        );
        return;
    }
    let mut cfg = Config::from_env().unwrap();
    cfg.cache = false;
    cfg.analytics = false;
    cfg.public_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../public");
    let app = App {
        pool: pool_from_env(&cfg).await.unwrap(),
        config: cfg.clone(),
    };
    assert!(
        app.pool
            .read()
            .await
            .unwrap()
            .rows::<serde_json::Value>(
                "UPDATE _migration_state SET syncing=syncing WHERE id=1",
                vec![]
            )
            .await
            .is_err()
    );
    let (status, h, html) = request(&app, "/").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(count(&h), 3); // states + grouped averages + ordered shelters
    assert!(html.contains("State average: 26&deg; / 12&deg;")); // (10+20+50)/3 and (-1-2+40)/3; excludes 2100
    assert!(html.contains("Middle &#38; &#60;Shelter&#62;"));
    assert!(html.contains("/shelters/2"));
    assert!(html.find("Start Shelter").unwrap() < html.find("Middle &#38;").unwrap());
    let (status, h, html) = request(&app, "/shelters/2").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(count(&h), 5); // shelter + daily + hourly + 2 neighbors
    assert_eq!(h["x-db-query-cache-hits"], "6");
    assert_eq!(html.matches("Previous Shelter</a>").count(), 2);
    assert_eq!(html.matches("Next Shelter</a>").count(), 2);
    assert_eq!(html.matches("No hourly weather available.").count(), 2);
    assert!(html.contains("Freeze Warning!"));
    assert!(html.contains("11:59 PM"));
    assert!(html.contains("post-category-rain"));
    assert!(html.contains("NOBO Mile 110.5"));
    let (status, h, body) = request(&app, "/shelters/2.json?api_key=fixture-key").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(count(&h), 4); // shelter + auth + daily + hourly
    let json: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(json[0]["mileage"], "110.5");
    assert_eq!(json[0]["southbound_mileage"], 2082.5);
    assert!(json[0]["daily_weather"][0]["hourly_weather"][0]["wind"].is_null());
    assert_eq!(json[0]["daily_weather"].as_array().unwrap().len(), 3);
    assert_eq!(
        json[0]["daily_weather"][0]["hourly_weather"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    assert_eq!(
        json[0]["daily_weather"][0]["weather_date"],
        "2020-01-01T12:00:00.000-05:00"
    );
    let (status, h, body) =
        request(&app, "/shelters/1.json?api_key=fixture-key&dist_miles=20").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(count(&h), 5); // anchor + auth + range + batched daily + batched hourly
    let json: Value = serde_json::from_str(&body).unwrap();
    let ids: Vec<_> = json
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v["shelter_id"].as_i64().unwrap())
        .collect();
    assert_eq!(ids.len(), 2);
    assert!(ids.contains(&2) && ids.contains(&3) && !ids.contains(&1));
    let (_, h, body) = request(&app, "/shelters/1.json?api_key=fixture-key&dist_miles=130").await;
    assert_eq!(count(&h), 5); // Five shelters still need only two forecast statements.
    let json: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(json.as_array().unwrap().len(), 5);
    for shelter in json.as_array().unwrap() {
        if matches!(shelter["shelter_id"].as_i64(), Some(5 | 6)) {
            assert!(shelter["daily_weather"].as_array().unwrap().is_empty());
        }
    }
    let (_, h, body) = request(&app, "/shelters/1.json?api_key=fixture-key&dist_miles=0").await;
    assert_eq!(count(&h), 3); // Empty range skips both forecast queries.
    assert_eq!(
        serde_json::from_str::<Value>(&body).unwrap(),
        serde_json::json!([])
    );
    let (_, h, body) = request(&app, "/index.json?api_key=fixture-key").await;
    assert_eq!(count(&h), 3);
    let json: Value = serde_json::from_str(&body).unwrap();
    assert!(json[0].get("shelters").is_none());
    let (_, h, body) = request(
        &app,
        "/index.json?api_key=fixture-key&include_shelters=true",
    )
    .await;
    assert_eq!(count(&h), 4);
    let json: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(json[0]["shelters"][0]["mileage"], "100.0");
    assert!(json[0]["shelters"][0]["latt"].is_null());
    assert!(json[0]["shelters"][0]["elevation"].is_null());
    assert_eq!(json[1]["average_high"], -3);
    assert_eq!(json[1]["average_low"], -4);
    assert_eq!(json[2]["average_high"], 0);
    assert_eq!(json[2]["average_low"], 0);
    assert_eq!(
        request(&app, "/index.json").await.0,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(&app, "/shelters/2.json?api_key=wrong").await.0,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(&app, "/shelters/999999.json").await.0,
        StatusCode::NOT_FOUND
    );
    let missing: Value =
        serde_json::from_str(&request(&app, "/shelters/999999.json").await.2).unwrap();
    assert_eq!(missing["status"], 404);
    let response = router(app.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/shelters/2")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::METHOD_NOT_ALLOWED);
    assert_eq!(
        request(&app, "/shelters/5").await.0,
        StatusCode::SERVICE_UNAVAILABLE
    );
    assert_eq!(
        request(&app, "/nearest_shelter?nobo_mile=110.5").await.2,
        html
    );
    assert_eq!(
        request(&app, "/nearest_shelter?coords=35.1,-81.1").await.2,
        html
    );
    assert_eq!(
        request(
            &app,
            "/nearest_shelter?nobo_mile=1%3BDROP%20TABLE%20shelters"
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        request(&app, "/nearest_shelter?coords=NaN,0").await.0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(request(&app, "/nearest_shelter").await.0, StatusCode::FOUND);
    assert_eq!(request(&app, "/about").await.0, StatusCode::OK);
    assert_eq!(request(&app, "/admin").await.0, StatusCode::NOT_FOUND);
    assert_eq!(
        request(&app, "/cached_pages/shelters/2.json").await.0,
        StatusCode::NOT_FOUND
    );
    let (status, h, _) = request(
        &app,
        &format!("/assets/application-7c3793d66b8fdd89438fbe0564edadeff835d8821d505f2f6483eb96f7f96c1b.css?v={}", at_forecast::asset_cache::VERSION),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(h["content-type"].to_str().unwrap().starts_with("text/css"));
    assert_eq!(h["cache-control"], "public, max-age=86400");
    let (status, h, _) = request(&app, "/favicon.ico").await;
    assert_eq!(status, StatusCode::TEMPORARY_REDIRECT);
    assert_eq!(
        h["location"],
        format!("/favicon.ico?v={}", at_forecast::asset_cache::VERSION)
    );
    assert_eq!(h["cache-control"], "no-store");

    let mut cached = app.clone();
    cached.config.cache = true;
    cached.config.cache_dir =
        std::env::temp_dir().join(format!("at-forecast-test-{}", std::process::id()));
    let _ = tokio::fs::remove_dir_all(&cached.config.cache_dir).await;
    let (_, h, first) = request(&cached, "/").await;
    assert_eq!(count(&h), 3);
    let (_, h, second) = request(&cached, "/").await;
    assert_eq!(count(&h), 0);
    assert_eq!(h["x-page-cache"], "hit");
    assert_eq!(first, second);
    std::fs::File::open(cached.config.cache_dir.join("index.html"))
        .unwrap()
        .set_modified(std::time::SystemTime::now() - std::time::Duration::from_secs(7202))
        .unwrap();
    assert_eq!(count(&request(&cached, "/").await.1), 3);
    // Shelter cache writes do not create a new serving cache. Every request still queries PostgreSQL.
    request(&cached, "/shelters/2").await;
    assert_eq!(count(&request(&cached, "/shelters/2").await.1), 5);
    request(&cached, "/shelters/2.json?api_key=fixture-key").await;
    assert_eq!(
        request(&cached, "/shelters/2.json").await.0,
        StatusCode::UNAUTHORIZED
    );
    tokio::fs::remove_dir_all(&cached.config.cache_dir)
        .await
        .unwrap();
    app.pool.close().await;
}
