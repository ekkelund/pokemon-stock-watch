#!/usr/bin/env python3
"""Undersøg hvordan produktlister kan hentes uden at køre JavaScript.

To spor:
  1. robots.txt og sitemap. Lavet til crawlere, derfor stabilt og tilladt.
  2. Nuxt-bundlerne, som kan afsløre hvilket søge-API gitteret henter fra.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import http_get  # noqa: E402

SITES = ["https://www.bilka.dk", "https://www.br.dk", "https://www.foetex.dk"]
HINTS = ["algolia", "relewise", "loop54", "typesense", "elasticsearch", "graphql",
         "occ/v2", "searchKey", "apiKey", "appId", "x-api-key", "/api/", "webapi",
         "productsearch", "product-search", "sallinggroup.com", "search?"]
URL_RE = re.compile(r'["\'](https?://[^"\']{8,120}|/[a-z0-9\-_/]{4,80}(?:search|product|api)[a-z0-9\-_/]{0,40})["\']',
                    re.IGNORECASE)


def show(label, text, limit=1200):
    print(f"--- {label} ---")
    print(text[:limit])
    print()


for site in SITES:
    print("=" * 78)
    print(site)
    print("=" * 78)
    robots, err = http_get(f"{site}/robots.txt")
    show("robots.txt", robots if robots else f"FEJL: {err}", 900)

    for candidate in ("/sitemap.xml", "/sitemap_index.xml"):
        body, err = http_get(f"{site}{candidate}")
        if body:
            locs = re.findall(r"<loc>([^<]+)</loc>", body)
            print(f"--- {candidate}: {len(body)} tegn, {len(locs)} <loc> ---")
            for loc in locs[:12]:
                print(f"    {loc}")
            print()
            break
        print(f"--- {candidate}: FEJL: {err} ---")
    print()

# Nuxt-bundlerne. Kun bilka, de tre sider deler platform.
page, _ = http_get("https://www.bilka.dk/boern-leg/legetoej/samlekort/pl/samlekort/")
if page:
    srcs = re.findall(r'<script\b[^>]*\bsrc=["\'](/_nuxt/[^"\']+)["\']', page)
    print("=" * 78)
    print(f"Nuxt-bundler: {srcs}")
    print("=" * 78)
    for src in srcs:
        js, err = http_get(f"https://www.bilka.dk{src}")
        if js is None:
            print(f"{src}: FEJL {err}")
            continue
        hits = {h: js.lower().count(h.lower()) for h in HINTS if h.lower() in js.lower()}
        print(f"\n{src}: {len(js)} tegn, træffere: {hits}")
        for hint in hits:
            for m in list(re.finditer(re.escape(hint), js, re.IGNORECASE))[:2]:
                start = max(0, m.start() - 110)
                print(f"    [{hint}] ...{js[start:m.end() + 110]}...")
        urls = sorted(set(URL_RE.findall(js)))[:25]
        if urls:
            print(f"    URL-kandidater ({len(urls)}):")
            for u in urls:
                print(f"      {u}")
