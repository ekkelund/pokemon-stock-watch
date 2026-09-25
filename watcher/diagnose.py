#!/usr/bin/env python3
"""Diagnoseværktøj til listesiderne.

Udviklingsmiljøet har ingen adgang til Sallings domæner, men Actions-runneren
har. Dette script køres derude og udskriver nok om en sides opbygning til at
afgøre hvordan produkterne kan aflæses.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import (  # noqa: E402
    NEXT_F_RE, PRODUCT_HREF_RE, http_get, parse_ld_json, parse_next_data,
)

SCRIPT_SRC_RE = re.compile(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
API_RE = re.compile(r'https?://[^\s"\'<>]*(?:api|graphql|search|/v\d)[^\s"\'<>]*', re.IGNORECASE)


def summarise(url: str) -> None:
    print("=" * 78)
    print(url)
    print("=" * 78)
    html, error = http_get(url)
    if html is None:
        print(f"HENTNING MISLYKKEDES: {error}\n")
        return
    print(f"længde: {len(html)} tegn")

    links = sorted(set(PRODUCT_HREF_RE.findall(html)))
    print(f"/produkter/-links i rå HTML: {len(links)}")
    for slug, pid in links[:10]:
        print(f"    {pid}  {slug}")

    print(f"\n__NEXT_DATA__ til stede: {'__NEXT_DATA__' in html}")
    data = parse_next_data(html)
    if data is not None:
        blob = json.dumps(data)
        print(f"  kunne parses, {len(blob)} tegn")
        print(f"  topnøgler: {list(data)[:12]}")
        props = data.get("props", {}).get("pageProps", {})
        if isinstance(props, dict):
            print(f"  pageProps-nøgler: {list(props)[:20]}")
        print(f"  /produkter/ i blobben: {len(set(PRODUCT_HREF_RE.findall(blob)))}")

    pushes = NEXT_F_RE.findall(html)
    print(f"\nself.__next_f-payloads: {len(pushes)}")
    if pushes:
        decoded = "".join(json.loads(p) for p in pushes)
        print(f"  afkodet længde: {len(decoded)}")
        print(f"  /produkter/ heri: {len(set(PRODUCT_HREF_RE.findall(decoded)))}")
        for needle in ('"name"', '"productId"', '"availability"', 'booster'):
            print(f"  forekomster af {needle}: {decoded.lower().count(needle.lower())}")

    blocks = parse_ld_json(html)
    print(f"\nJSON-LD-blokke: {len(blocks)}")
    for block in blocks:
        types = block.get("@type")
        print(f"  @type={types}")
        print(f"    {json.dumps(block, ensure_ascii=False)[:600]}")

    hosts = sorted({re.sub(r'^(https?://[^/]+).*', r'\1', s)
                    for s in SCRIPT_SRC_RE.findall(html) if s.startswith("http")})
    print(f"\nscript-værter: {hosts[:10]}")

    apis = sorted(set(API_RE.findall(html)))
    print(f"kandidat-API-URL'er: {len(apis)}")
    for api in apis[:15]:
        print(f"    {api[:160]}")

    print("\n--- første 700 tegn ---")
    print(html[:700].replace("\n", " "))
    print()


if __name__ == "__main__":
    urls = sys.argv[1:]
    if not urls:
        cfg = json.loads((Path(__file__).resolve().parent / "targets.json").read_text())
        urls = [t["url"] for t in cfg["listings"]]
    for url in urls:
        summarise(url)
