FROM rust:1.99-slim-trixie AS builder
WORKDIR /build
COPY rust/ ./rust/
RUN cargo build --locked --release --manifest-path rust/Cargo.toml

FROM debian:trixie-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder /build/rust/target/release/at-forecast /usr/local/bin/at-forecast
COPY public/assets/ ./public/assets/
COPY public/favicon.ico public/robots.txt public/apple-touch-icon.png public/apple-touch-icon-precomposed.png ./public/
ENV PORT=8080 PUBLIC_DIR=/app/public CACHE_DIR=/tmp/at-forecast-cache
USER 10001:10001
EXPOSE 8080
CMD ["at-forecast"]
