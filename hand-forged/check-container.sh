#!/bin/bash
set -euo pipefail
mkdir -p hand-forged/test-results
start=$(date +%s)
export HAND_FORGED_STARTED_AT="$(python3 -c 'import time;print(time.time())')"
cleanup() { docker logs hf-web > hand-forged/test-results/application.log 2>&1 || true; docker logs hf-database > hand-forged/test-results/database.log 2>&1 || true; docker rm -f hf-web hf-database >/dev/null 2>&1 || true; }
trap cleanup EXIT
launch() {
 docker run -d --name hf-database --memory=256m --cpus=1 --tmpfs /var/lib/postgresql/data:size=192m -p 127.0.0.1:8080:8080 hand-forged-db
 docker run -d --name hf-web --memory=256m --cpus=1 --network container:hf-database -e HAND_FORGED_LOCAL_TEST=true hand-forged-web
}
launch
python3 hand-forged/smoke.py
node hand-forged/browser.mjs
ready=$(date +%s)
echo "startup_and_checks_seconds=$((ready-start))" > hand-forged/test-results/measurements.txt
for name in hf-web hf-database; do docker exec "$name" sh -c 'cat /sys/fs/cgroup/memory.peak 2>/dev/null || cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes' | sed "s/^/$name peak_bytes=/" >> hand-forged/test-results/measurements.txt; done
docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' hf-web hf-database >> hand-forged/test-results/measurements.txt
docker image inspect --format '{{.RepoTags}} {{.Size}}' hand-forged-web hand-forged-db >> hand-forged/test-results/measurements.txt
# Write a marker into writable container storage; a replacement must lose it.
docker exec hf-web sh -c 'echo disposable > tmp/hand-forged-reset-marker'
docker exec hf-database psql -U postgres -d fast_at_weather_production -v ON_ERROR_STOP=1 -c "INSERT INTO states(name,abbreviation,created_at,updated_at) VALUES('Reset marker','ZZ',NOW(),NOW())"
docker stop -t 10 hf-web hf-database
test "$(docker inspect --format '{{.State.ExitCode}}' hf-web)" = 0
test "$(docker inspect --format '{{.State.ExitCode}}' hf-database)" = 0
docker inspect --format '{{.Name}} exit={{.State.ExitCode}}' hf-web hf-database >> hand-forged/test-results/measurements.txt
docker logs hf-web > hand-forged/test-results/application.log 2>&1
docker logs hf-database > hand-forged/test-results/database.log 2>&1
docker logs hf-web > hand-forged/test-results/first-shutdown.log 2>&1
cleanup
launch
for i in $(seq 1 180); do curl --fail --silent http://127.0.0.1:8080/ >/dev/null && break || sleep 1; done
curl --fail --silent http://127.0.0.1:8080/ -o hand-forged/test-results/reset-home.html
grep -q 'Georgia' hand-forged/test-results/reset-home.html
! grep -q 'Hand-Forged Edition' hand-forged/test-results/reset-home.html
docker exec hf-web test ! -e tmp/hand-forged-reset-marker
count=$(docker exec hf-database psql -U postgres -d fast_at_weather_production -tAc 'SELECT COUNT(*) FROM states')
test "$count" = 13
count=$(docker exec hf-database psql -U postgres -d fast_at_weather_production -tAc 'SELECT COUNT(*) FROM shelters')
test "$count" = 281
test -z "$(docker port hf-database 5432/tcp)"
echo 'PASS replaced instance restores seed and loses writable files; PostgreSQL port is unpublished'
