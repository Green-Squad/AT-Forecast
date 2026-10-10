use at_forecast::{App, Config, pool_from_env, router};
use std::{env, time::Instant};
#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(tracing_subscriber::EnvFilter::new(
            env::var("RUST_LOG")
                .unwrap_or_else(|_| "at_forecast=info,tower_http=warn,sqlx=off".into()),
        ))
        .init();
    if let Err(message) = run().await {
        tracing::error!(reason = message, "startup failed");
        std::process::exit(1);
    }
}
async fn run() -> Result<(), String> {
    let start = Instant::now();
    let config = Config::from_env()?;
    let pool = pool_from_env(&config).await?;
    let port = env::var("PORT")
        .unwrap_or_else(|_| "8080".into())
        .parse::<u16>()
        .map_err(|_| "Invalid PORT".to_string())?;
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port))
        .await
        .map_err(|_| "Unable to bind port".to_string())?;
    tracing::info!(
        port,
        ready_ms = start.elapsed().as_secs_f64() * 1000.0,
        cache = if config.cache { "rails" } else { "off" },
        "listening"
    );
    axum::serve(
        listener,
        router(App {
            pool: pool.clone(),
            config,
        }),
    )
    .with_graceful_shutdown(async {
        #[cfg(unix)]
        {
            let mut term =
                tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                    .expect("SIGTERM handler");
            tokio::select! {_=tokio::signal::ctrl_c()=>{},_=term.recv()=>{}}
        }
        #[cfg(not(unix))]
        {
            let _ = tokio::signal::ctrl_c().await;
        }
        tracing::info!("shutdown requested");
    })
    .await
    .map_err(|_| "HTTP server failed".to_string())?;
    pool.close().await;
    Ok(())
}
