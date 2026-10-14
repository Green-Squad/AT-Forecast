#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter("at_forecast=info")
        .init();
    match at_forecast::weather::run_from_env().await {
        Ok(report) => tracing::info!(report=%report,"weather job completed"),
        Err(error) => {
            tracing::error!(
                category = error.0,
                "weather job failed; active generation remains complete"
            );
            std::process::exit(1);
        }
    }
}
