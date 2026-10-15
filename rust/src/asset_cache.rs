//! Release-keyed public assets. Unversioned dependency URLs always resolve to
//! the current release; HTML/API/account routes never receive this middleware.
use axum::{
    body::Body,
    extract::{OriginalUri, Request},
    http::{HeaderValue, Method, StatusCode, header},
    middleware::Next,
    response::{IntoResponse, Response},
};
pub const VERSION: &str = match option_env!("ASSET_VERSION") {
    Some(version) => version,
    None => "development",
};
pub fn url(path: &str) -> String {
    if path.is_empty() || path.starts_with("/rust-media/") || path.starts_with("data:") {
        return path.into();
    }
    let (path, fragment) = path.split_once('#').unwrap_or((path, ""));
    let separator = if path.contains('?') { '&' } else { '?' };
    let fragment = if fragment.is_empty() {
        String::new()
    } else {
        format!("#{fragment}")
    };
    format!("{path}{separator}v={VERSION}{fragment}")
}
fn versioned(query: Option<&str>) -> bool {
    query.is_some_and(|q| {
        q.split('&').any(|p| {
            p.split_once('=')
                .is_some_and(|(k, v)| k == "v" && !v.is_empty())
        })
    })
}
pub async fn serve(request: Request<Body>, next: Next) -> Response {
    if request.method() != Method::GET && request.method() != Method::HEAD {
        return next.run(request).await;
    }
    let uri = request
        .extensions()
        .get::<OriginalUri>()
        .map(|u| &u.0)
        .unwrap_or(request.uri());
    if !versioned(uri.query()) {
        let location = url(uri
            .path_and_query()
            .map(|p| p.as_str())
            .unwrap_or(uri.path()));
        let mut response = (
            StatusCode::TEMPORARY_REDIRECT,
            [(header::LOCATION, location)],
        )
            .into_response();
        response
            .headers_mut()
            .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
        return response;
    }
    let mut response = next.run(request).await;
    if response.status().is_success() || response.status() == StatusCode::NOT_MODIFIED {
        response
            .headers_mut()
            .entry(header::CACHE_CONTROL)
            .or_insert(HeaderValue::from_static("public, max-age=86400"));
    } else {
        response
            .headers_mut()
            .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    }
    response
        .headers_mut()
        .insert("x-asset-version", HeaderValue::from_static(VERSION));
    response
}
#[cfg(test)]
mod tests {
    use super::*;
    use axum::{Router, middleware, routing::get};
    use tower::ServiceExt;
    #[tokio::test]
    async fn dependencies_get_current_release_keys_and_errors_are_not_cached() {
        let app = Router::new().nest(
            "/fonts",
            Router::new()
                .route("/font.woff", get(|| async { "font" }))
                .route("/missing.woff", get(|| async { StatusCode::NOT_FOUND }))
                .layer(middleware::from_fn(serve)),
        );
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/fonts/font.woff?format=x")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
        assert_eq!(
            response.headers()[header::LOCATION],
            format!("/fonts/font.woff?format=x&v={VERSION}")
        );
        assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/fonts/font.woff?v={VERSION}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers()[header::CACHE_CONTROL],
            "public, max-age=86400"
        );
        let response = app
            .oneshot(
                Request::builder()
                    .uri(format!("/fonts/missing.woff?v={VERSION}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
    }
    #[test]
    fn fragments_and_content_addressed_uploads_keep_their_meaning() {
        assert_eq!(
            url("/fonts/a.eot?#iefix"),
            format!("/fonts/a.eot?&v={VERSION}#iefix")
        );
        assert_eq!(url("/rust-media/hash.png"), "/rust-media/hash.png");
        assert_eq!(url(""), "");
        assert!(!versioned(Some("preview=x")));
        assert!(!versioned(Some("v=")));
    }
}
