#!/usr/bin/env python3
"""Undersøg sitemap'et som vej til produktlisterne.

robots.txt peger på /sitemap/sitemap-index.xml. Sitemaps er udgivet netop
for at crawlere må læse dem, så det er den rette vej frem for at efterligne
sidens interne kald.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import http_get  # noqa: E402

SITES = ["https://www.bilka.dk", "https://www.br.dk", "https://www.foetex.dk"]
LOC_RE = re.compile(r"<loc>([^<]+)</loc>")


for base in SITES:
    print("=" * 78)
    print(base)
    print("=" * 78)
    index, err = http_get(f"{base}/sitemap/sitemap-index.xml")
    if index is None:
        print(f"sitemap-index: FEJL {err}\n")
        continue
    children = LOC_RE.findall(index)
    print(f"sitemap-index: {len(index)} tegn, {len(children)} undersitemaps")
    for child in children[:25]:
        print(f"    {child}")

    # Gennemgå ALLE undersitemaps: produkterne ligger ikke nødvendigvis i det
    # første, og det er netop produkt-URLerne der skal kunne opdages.
    all_products = []
    for child in children:
        body, err = http_get(child)
        if body is None:
            print(f"  {child}: FEJL {err}")
            continue
        locs = LOC_RE.findall(body)
        products = [l for l in locs if "/produkter/" in l]
        all_products.extend(products)
        print(f"  {child.rsplit('/', 1)[1]}: {len(locs)} URLer, heraf {len(products)} produkter")
        if products:
            print(f"    eksempel: {products[0]}")

    print(f"\n  produkter i alt: {len(all_products)}")
    for label, pattern in [("pokemon", r"pokemon"),
                           ("booster", r"booster"),
                           ("booster bundle", r"booster.{0,15}bundle"),
                           ("elite trainer", r"elite.{0,10}trainer")]:
        hits = [l for l in all_products if re.search(pattern, l, re.IGNORECASE)]
        print(f"  {label}: {len(hits)}")
        for h in hits[:4]:
            print(f"      {h}")
    print()
