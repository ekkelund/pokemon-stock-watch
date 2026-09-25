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

    # Kig i det første der ser ud til at rumme produkter.
    product_maps = [c for c in children if "produkt" in c.lower() or "product" in c.lower()]
    target = product_maps[0] if product_maps else (children[0] if children else None)
    if not target:
        print()
        continue

    print(f"\n  henter: {target}")
    body, err = http_get(target)
    if body is None:
        print(f"  FEJL {err}\n")
        continue
    locs = LOC_RE.findall(body)
    print(f"  {len(body)} tegn, {len(locs)} URLer")
    for loc in locs[:5]:
        print(f"    {loc}")
    bundles = [l for l in locs if re.search(r"booster.{0,15}bundle", l, re.IGNORECASE)]
    print(f"  heraf booster bundle: {len(bundles)}")
    for b in bundles[:10]:
        print(f"    {b}")
    print()
