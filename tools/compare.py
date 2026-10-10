#!/usr/bin/env python3
"""Compare public responses on TWO LOCAL isolated servers sharing the fixture database."""
import argparse, html, json, os, re, urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit, quote

class Content(HTMLParser):
    def __init__(self):
        super().__init__(); self.ignore = 0; self.text = []; self.links = []; self.in_content = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'): self.ignore += 1
        if tag == 'a' and 'href' in attrs:
            href = attrs['href']; parsed = urlsplit(href)
            self.links.append(parsed.path + ('?' + parsed.query if parsed.query else '') if parsed.hostname in ('localhost','127.0.0.1') else href)
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.ignore -= 1
    def handle_data(self, data):
        if not self.ignore: self.text.extend(data.split())

def fetch(base, path):
    try:
        response = urllib.request.urlopen(base.rstrip('/') + path, timeout=30)
    except urllib.error.HTTPError as e:
        response = e
    return response.status, response.read().decode(), response.headers

p = argparse.ArgumentParser(); p.add_argument('--rails', required=True); p.add_argument('--rust', required=True)
a = p.parse_args()
for base in (a.rails, a.rust):
    if urlsplit(base).hostname not in ('localhost','127.0.0.1','::1'): p.error('Comparison only permits local isolated servers')
key = quote(os.environ.get('BENCH_API_KEY','fixture-key'), safe='')
paths = ['/', '/about', '/shelters/2', '/nearest_shelter?nobo_mile=110.5', '/nearest_shelter?coords=35.1,-81.1',
    '/shelters/999999', '/index.json', '/shelters/2.json?api_key=wrong',
    '/index.json?api_key=' + key, '/index.json?include_shelters=true&api_key=' + key,
    '/shelters/2.json?api_key=' + key, '/shelters/1.json?dist_miles=20&api_key=' + key]
for path in paths:
    rails, rust = fetch(a.rails,path), fetch(a.rust,path)
    assert rails[0] == rust[0], 'Status differs for ' + path.split('?')[0]
    if '.json' in path:
        assert json.loads(rails[1]) == json.loads(rust[1]), 'JSON differs for ' + path.split('?')[0]
    elif rails[0] == 200:
        x,y = Content(),Content(); x.feed(rails[1]); y.feed(rust[1])
        assert x.text == y.text, 'Visible text differs for ' + path.split('?')[0]
        assert x.links == y.links, 'Links differ for ' + path.split('?')[0]
    # Error copy and CSRF tokens intentionally differ; no write endpoints exist in Rust.
    print('PASS', path.split('?')[0], rails[0])
