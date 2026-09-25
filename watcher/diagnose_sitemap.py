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

DEFAULT_SITES = ["https://www.bilka.dk", "https://www.br.dk", "https://www.foetex.dk"]
LOC_RE = re.compile(r"<loc>([^<]+)</loc>")

# Vare-id for Elite Trainer Box 30th. Går igen på tværs af koncernens sider,
# så den afslører om et nyt site deler varenummer-rum med de kendte.
KNOWN_PRODUCT_ID = "200392202"


# Et foreslået mønster kan prøves af mod de rigtige kataloger inden det sættes
# i drift. Falske træf er dyre: de lærer en at ignorere notifikationerne.
args = sys.argv[1:]
try_match = try_exclude = None
if "--match" in args:
    i = args.index("--match"); try_match = args[i + 1]; del args[i:i + 2]
if "--exclude" in args:
    i = args.index("--exclude"); try_exclude = args[i + 1]; del args[i:i + 2]

for base in (args or DEFAULT_SITES):
    print("=" * 78)
    print(base)
    print("=" * 78)
    robots, err = http_get(f"{base}/robots.txt")
    if robots is None:
        print(f"robots.txt: FEJL {err}")
    else:
        sitemaps = re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", robots)
        disallows = re.findall(r"(?im)^\s*Disallow:\s*(\S+)", robots)
        print(f"robots.txt: {len(disallows)} Disallow, sitemap-henvisninger: {sitemaps}")

    # Følg robots.txt' egen henvisning frem for en fast sti: koncernens sider
    # ligger ikke alle på samme platform, og robots.txt er standardmåden at
    # finde et sitemap på.
    candidates = sitemaps if robots else []
    candidates.append(f"{base}/sitemap/sitemap-index.xml")
    candidates.append(f"{base}/sitemap.xml")

    index = None
    for candidate in candidates:
        index, err = http_get(candidate)
        if index is not None:
            print(f"sitemap fundet: {candidate}")
            break
        print(f"  {candidate}: {err}")
    if index is None:
        print()
        continue
    children = LOC_RE.findall(index)
    # Et sitemap-index peger på andre sitemaps; et almindeligt sitemap peger
    # direkte på sider. Kender vi ikke typen, behandler vi det som et index
    # hvis alle henvisninger selv ser ud som sitemaps.
    if "<sitemapindex" not in index[:400].lower():
        children = [candidate] if not children else children
        if "<urlset" in index[:400].lower():
            children = [candidate]
    print(f"sitemap-index: {len(index)} tegn, {len(children)} undersitemaps")
    for child in children[:25]:
        print(f"    {child}")

    # Gennemgå ALLE undersitemaps: produkterne ligger ikke nødvendigvis i det
    # første, og det er netop produkt-URLerne der skal kunne opdages.
    all_urls = []
    for child in children[:12]:
        body, err = http_get(child)
        if body is None:
            print(f"  {child}: FEJL {err}")
            continue
        locs = LOC_RE.findall(body)
        all_urls.extend(locs)
        print(f"  {child.rsplit('/', 1)[1]}: {len(locs)} URLer")

    # Hvilke stier findes overhovedet? Et ukendt site bruger ikke nødvendigvis
    # /produkter/, så segmenterne tælles frem for at blive antaget.
    from collections import Counter
    segments = Counter()
    for url in all_urls:
        parts = [p for p in url.split("/")[3:] if p]
        segments[parts[0] if parts else "(rod)"] += 1
    print(f"\n  hyppigste førstesegment: {segments.most_common(12)}")

    all_products = [l for l in all_urls if "/produkter/" in l or "/produkt/" in l]
    print(f"  URLer med /produkter/ eller /produkt/: {len(all_products)}")
    if not all_products:
        all_products = all_urls
        print("  (ingen produktsti genkendt, søger i alle URLer)")

    print(f"\n  produkter i alt: {len(all_products)}")
    known = [l for l in all_products if KNOWN_PRODUCT_ID in l]
    print(f"  kender vare {KNOWN_PRODUCT_ID} (ETB 30th): {'JA' if known else 'nej'}")
    for k in known[:2]:
        print(f"      {k}")

    if try_match:
        # Der matches på slug'en alene, præcis som overvågningen gør.
        def slug(url):
            parts = [p for p in url.split("/")[3:] if p]
            if len(parts) > 1 and re.match(r"^(?:p-)?\d{4,}$", parts[-1]):
                return parts[-2]
            return parts[-1] if parts else ""

        hits = [u for u in all_products
                if re.search(try_match, slug(u).replace("-", " "), re.IGNORECASE)]
        kept, dropped = [], []
        for u in hits:
            target = dropped if (try_exclude and re.search(
                try_exclude, slug(u).replace("-", " "), re.IGNORECASE)) else kept
            target.append(u)
        print(f"  MØNSTER {try_match!r}: {len(hits)} træf")
        for u in kept:
            print(f"      BEHOLDT  {slug(u)}")
        for u in dropped:
            print(f"      FRASORT. {slug(u)}")
    else:
        for label, pattern in [("pokemon", r"pokemon"),
                               ("booster", r"booster"),
                               ("booster bundle", r"booster.{0,15}bundle"),
                               ("elite trainer", r"elite.{0,10}trainer")]:
            hits = [l for l in all_products if re.search(pattern, l, re.IGNORECASE)]
            print(f"  {label}: {len(hits)}")
            for h in hits[:4]:
                print(f"      {h}")
    print()
