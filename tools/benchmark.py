#!/usr/bin/env python3
"""Benchmark only a local/isolated service. Python standard library; no CDN/browser cache."""
import argparse, concurrent.futures, http.client, json, math, os, statistics, time
from urllib.parse import urlsplit

p = argparse.ArgumentParser()
p.add_argument('--base', default='http://127.0.0.1:8080')
p.add_argument('--route', action='append', default=[])
p.add_argument('--requests', type=int, default=200)
p.add_argument('--concurrency', type=int, default=1)
p.add_argument('--warmup', type=int, default=10)
p.add_argument('--allow-isolated-host', action='store_true', help='Explicitly attest a nonlocal host is isolated')
a = p.parse_args()
u = urlsplit(a.base)
if u.hostname not in ('localhost', '127.0.0.1', '::1') and not a.allow_isolated_host:
    p.error('Use a local service, or explicitly attest --allow-isolated-host. Never target production.')
if not (1 <= a.concurrency <= 10 and 1 <= a.requests <= 10000 and 0 <= a.warmup <= 100):
    p.error('Use concurrency 1..10, requests 1..10000, warmup 0..100')
routes = a.route or ['/', '/shelters/2', '/nearest_shelter?nobo_mile=110.5', '/about']
key = os.environ.get('BENCH_API_KEY')

def one(route):
    request_path = route.replace('{API_KEY}', __import__('urllib.parse', fromlist=['quote']).quote(key or '', safe=''))
    conn_cls = http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port, timeout=30)
    begin = time.perf_counter()
    try:
        conn.request('GET', (u.path.rstrip('/') + request_path), headers={'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity'})
        response = conn.getresponse()
        body = response.read()
        elapsed = (time.perf_counter() - begin) * 1000
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}; benchmark aborted')
        timing = response.getheader('Server-Timing', '')
        db = next((float(part.split('dur=')[1].split(';')[0]) for part in timing.split(',') if part.strip().startswith('db;') and 'dur=' in part), None)
        queries = response.getheader('X-DB-Query-Count')
        return elapsed, int(queries) if queries is not None else None, db, len(body), response.getheader('X-Page-Cache', 'unknown')
    finally:
        conn.close()

def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]

results = []
for route in routes:
    if '{API_KEY}' in route and not key:
        p.error('Set BENCH_API_KEY for authenticated routes; the key is never printed')
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as executor:
        list(executor.map(one, [route] * a.warmup))
        begin = time.perf_counter()
        samples = list(executor.map(one, [route] * a.requests))
        wall = time.perf_counter() - begin
    lat = [s[0] for s in samples]
    counts = [s[1] for s in samples if s[1] is not None]
    db = [s[2] for s in samples if s[2] is not None]
    results.append({'route': route, 'requests': a.requests, 'concurrency': a.concurrency,
        'p50_ms': percentile(lat, .5), 'p95_ms': percentile(lat, .95), 'requests_per_second': len(samples) / wall,
        'query_counts': sorted(set(counts)), 'db_ms_mean': statistics.mean(db) if db else None,
        'bytes': sorted(set(s[3] for s in samples)), 'page_cache': sorted(set(s[4] for s in samples)),
        'connections': 'new TCP connection per request', 'transport': u.scheme})
print(json.dumps(results, indent=2))
