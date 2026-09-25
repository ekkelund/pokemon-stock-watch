#!/usr/bin/env python3
"""Lagerovervågning af Pokémon-samlekort hos Salling Group (bilka.dk, foetex.dk, br.dk).

Kører som GitHub Actions-cron og sender ntfy-notifikation når noget bliver
tilgængeligt. Kun stdlib, så jobbet starter uden pip install.

Sider hos Salling er Next.js-drevne, og deres præcise DOM/JSON-struktur er ikke
dokumenteret. Derfor er lageraflæsningen bygget som en kaskade af metoder fra
mest til mindst pålidelig, og resultatet bærer altid metoden med sig, så en
notifikation kan læses med det rette forbehold. Slår alle metoder fejl, bliver
status "unknown" og der sendes en diagnose-notifikation. Det er med vilje:
en scraper der stille rapporterer "udsolgt" for evigt fordi markuppen er
ændret, er værre end en der siger højt at den er gået i stykker.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Copenhagen")
except Exception:  # pragma: no cover - zoneinfo findes på ubuntu-latest
    TZ = timezone(timedelta(hours=1))

HERE = Path(__file__).resolve().parent
TARGETS_PATH = HERE / "targets.json"
STATE_PATH = HERE / "state.json"

IN_STOCK = "in_stock"
OUT_OF_STOCK = "out_of_stock"
UNKNOWN = "unknown"

# Hvor længe der mindst går mellem to diagnose-notifikationer for samme mål.
# Uden det ville en blokeret side spamme telefonen hvert 30. minut.
DIAG_COOLDOWN_HOURS = 12

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_get(url: str, timeout: int = 30, attempts: int = 3):
    """Hent en side. Returnerer (html, fejltekst). Præcis én af dem er None."""
    last_err = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(2 ** attempt)
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in encoding:
                    raw = gzip.decompress(raw)
                elif "deflate" in encoding:
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace"), None
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code} {exc.reason}"
            # 4xx bortset fra 429 bliver ikke bedre af at prøve igen.
            if exc.code != 429 and 400 <= exc.code < 500:
                break
        except Exception as exc:  # netværk, timeout, TLS
            last_err = f"{type(exc).__name__}: {exc}"
    return None, last_err or "ukendt fejl"


# --------------------------------------------------------------------------
# Udtrækning af indlejrede data
# --------------------------------------------------------------------------

SCRIPT_RE = re.compile(
    r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
LDJSON_RE = re.compile(
    r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
NEXT_DATA_RE = re.compile(
    r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
# Next.js App Router streamer data som self.__next_f.push([1,"<escaped json>"]).
NEXT_F_RE = re.compile(r'self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*("(?:\\.|[^"\\])*")')
TAG_RE = re.compile(r"<[^>]+>")


def parse_ld_json(html: str) -> list:
    out = []
    for blob in LDJSON_RE.findall(html):
        try:
            data = json.loads(blob.strip())
        except Exception:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def parse_next_data(html: str):
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except Exception:
        return None


def harvest_script_text(html: str) -> str:
    """Al tekst fra <script>-tags, med App Router-payloads af-escapet.

    Bruges som sidste struktur-baserede net: selv når JSON'en ikke kan parses
    som ét dokument, står nøgle/værdi-parrene stadig i teksten.
    """
    parts = []
    for literal in NEXT_F_RE.findall(html):
        try:
            parts.append(json.loads(literal))
        except Exception:
            pass
    for body in SCRIPT_RE.findall(html):
        parts.append(body)
    return "\n".join(parts)


def walk_json(node, path=""):
    """Gennemløb al JSON og yield (sti, nøgle, værdi) for bladværdier."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(value, (dict, list)):
                yield from walk_json(value, child)
            else:
                yield child, str(key), value
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = f"{path}[{index}]"
            if isinstance(value, (dict, list)):
                yield from walk_json(value, child)
            else:
                yield child, "", value


OG_IMAGE_RE = re.compile(
    r'<meta\b[^>]*(?:property|name)=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _first_image_url(value):
    """Første brugbare billed-URL i en schema.org image-værdi.

    Feltet kan være en streng, en liste af strenge, eller ImageObject'er med
    url/contentUrl. Salling bruger en liste, og læses den ikke, falder vi
    tilbage på og:image, som er en bred beskæring til sociale medier og
    dermed et dårligere billede end det der lå lige for.
    """
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("url", "contentUrl"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None
    if isinstance(value, list):
        for item in value:
            found = _first_image_url(item)
            if found:
                return found
    return None


def _image_in_node(node):
    """Find et image-felt hvor som helst i en JSON-LD-blok, også under @graph."""
    if isinstance(node, dict):
        if "image" in node:
            found = _first_image_url(node["image"])
            if found:
                return found
        for value in node.values():
            found = _image_in_node(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _image_in_node(item)
            if found:
                return found
    return None


def detect_image(html: str, base_url: str):
    """Find produktets billede, så notifikationen viser den faktiske vare.

    JSON-LD foretrækkes: "image" på et Product er varen selv, mens og:image
    på en produktside er den beskårne udgave til sociale medier og på en
    listeside lige så godt kan være butikkens logo.
    """
    for entry in parse_ld_json(html):
        found = _image_in_node(entry)
        if found:
            return urllib.parse.urljoin(base_url, found)
    match = OG_IMAGE_RE.search(html)
    if match:
        return urllib.parse.urljoin(base_url, match.group(1).strip())
    return None


def visible_text(html: str) -> str:
    """HTML uden script/style, til de tekstbaserede heuristikker."""
    body = re.sub(
        r"<(script|style|noscript)\b[^>]*>.*?</\1>", " ", html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", body))


# --------------------------------------------------------------------------
# Lagerstatus
# --------------------------------------------------------------------------

# schema.org-værdier. InStoreOnly tæller med: varen er faktisk kommet på hylden,
# hvilket er præcis det "ny sending" betyder, selv om den ikke kan sendes.
AVAIL_IN = {"instock", "limitedavailability", "onlineonly", "instoreonly"}
AVAIL_OUT = {"outofstock", "soldout", "discontinued"}
AVAIL_SOON = {"preorder", "presale", "backorder"}

STOCK_KEY_RE = re.compile(
    r"(instock|isinstock|inventory|stocklevel|stockstatus|stockcount|"
    r"soldout|outofstock|availability|isavailable|available|"
    r"purchasable|canaddtocart|buyable)$",
    re.IGNORECASE,
)

# Rækkefølgen betyder noget: "ikke på lager" skal ramme før "på lager".
TEXT_OUT = [
    "midlertidigt udsolgt", "desværre udsolgt", "ikke på lager", "ikke paa lager",
    "udgået af sortimentet", "kan ikke bestilles", "ikke tilgængelig",
    "ude af lager", "forventes på lager", "vare på vej", "udsolgt",
    "giv besked når varen er på lager", "få besked når varen er på lager",
]
TEXT_IN = [
    "læg i kurv", "laeg i kurv", "læg i indkøbskurv", "tilføj til kurv",
    "på lager", "paa lager", "klar til afhentning", "kan afhentes",
]


def normalise_availability(value: str):
    token = re.sub(r"[^a-z]", "", str(value).lower().split("/")[-1])
    if token in AVAIL_IN:
        return IN_STOCK
    if token in AVAIL_OUT:
        return OUT_OF_STOCK
    if token in AVAIL_SOON:
        return OUT_OF_STOCK
    return None


def status_from_ldjson(html: str):
    """Lagerstatus fra schema.org offers.availability.

    offers er ofte en liste med én post pr. variant: salling.dk har en offer
    pr. størrelse. Én tilgængelig variant betyder at varen kan købes, så vi
    ser på dem alle frem for kun den første. Ellers ville en udsolgt str. S
    skjule en str. M på lager.
    """
    found = []
    for entry in parse_ld_json(html):
        for path, key, value in walk_json(entry):
            if key.lower() != "availability":
                continue
            status = normalise_availability(value)
            if status:
                found.append((status, f"{path} = {value}"))
    if not found:
        return None
    for status, evidence in found:
        if status == IN_STOCK:
            extra = f" (1 af {len(found)} varianter)" if len(found) > 1 else ""
            return IN_STOCK, "json-ld", evidence + extra
    return OUT_OF_STOCK, "json-ld", found[0][1]


def status_from_embedded_json(html: str):
    data = parse_next_data(html)
    if data is None:
        return None
    votes = []
    for path, key, value in walk_json(data):
        if not STOCK_KEY_RE.search(key):
            continue
        evidence = f"{path} = {value!r}"
        if isinstance(value, bool):
            negative_key = re.search(r"soldout|outofstock", key, re.IGNORECASE)
            in_stock = (not value) if negative_key else value
            votes.append((IN_STOCK if in_stock else OUT_OF_STOCK, evidence))
        elif isinstance(value, (int, float)) and "stock" in key.lower():
            votes.append((IN_STOCK if value > 0 else OUT_OF_STOCK, evidence))
        elif isinstance(value, str):
            status = normalise_availability(value)
            if status:
                votes.append((status, evidence))
    if not votes:
        return None
    # Ét troværdigt "på lager" vejer tungere end mange generiske felter, men
    # hvis flertallet siger udsolgt, følger vi flertallet.
    out_votes = sum(1 for status, _ in votes if status == OUT_OF_STOCK)
    in_votes = len(votes) - out_votes
    winner = IN_STOCK if in_votes > out_votes else OUT_OF_STOCK
    evidence = "; ".join(e for s, e in votes if s == winner)[:300]
    return winner, "embedded-json", evidence


def status_from_script_text(html: str):
    text = harvest_script_text(html)
    if not text:
        return None
    match = re.search(r'"availability"\s*:\s*"([^"]{1,80})"', text)
    if match:
        status = normalise_availability(match.group(1))
        if status:
            return status, "script-text", f'availability = {match.group(1)}'
    match = re.search(r'"(?:isInStock|inStock)"\s*:\s*(true|false)', text)
    if match:
        in_stock = match.group(1) == "true"
        return (
            IN_STOCK if in_stock else OUT_OF_STOCK,
            "script-text",
            f"inStock = {match.group(1)}",
        )
    match = re.search(r'"(?:soldOut|outOfStock)"\s*:\s*(true|false)', text)
    if match:
        sold_out = match.group(1) == "true"
        return (
            OUT_OF_STOCK if sold_out else IN_STOCK,
            "script-text",
            f"soldOut = {match.group(1)}",
        )
    return None


def status_from_text(html: str):
    """Sidste udvej. Lav tiltro: knapper kan stå i DOM'en selv når de er slået fra."""
    text = visible_text(html).lower()
    for phrase in TEXT_OUT:
        if phrase in text:
            return OUT_OF_STOCK, "text", phrase
    for phrase in TEXT_IN:
        if phrase in text:
            return IN_STOCK, "text", phrase
    return None


def detect_status(html: str):
    """Kør kaskaden. Returnerer (status, metode, belæg)."""
    for probe in (
        status_from_ldjson,
        status_from_embedded_json,
        status_from_script_text,
        status_from_text,
    ):
        result = probe(html)
        if result:
            return result
    return UNKNOWN, "none", "ingen af metoderne kunne aflæse lagerstatus"


# --------------------------------------------------------------------------
# Produkt-URLer
# --------------------------------------------------------------------------

PRODUCT_HREF_RE = re.compile(r'/produkter/([a-z0-9\-]+)/(\d+)/?', re.IGNORECASE)


# --------------------------------------------------------------------------
# ntfy
# --------------------------------------------------------------------------

def topic_fingerprint(topic: str) -> str:
    """Kort hash af topicet, så en forkert secret kan opdages i loggen.

    Selve topicet må ikke stå i loggen: repoet er offentligt, og alle der
    kender navnet kan både læse og sende til det.
    """
    return hashlib.sha256(topic.encode()).hexdigest()[:10]


def notify(title: str, message: str, *, priority: int = 5, tags=None,
           click: str | None = None, image: str | None = None,
           dry_run: bool = False) -> None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
    token = os.environ.get("NTFY_TOKEN", "").strip()

    if dry_run or not topic:
        why = "dry-run" if dry_run else "NTFY_TOPIC ikke sat"
        print(f"  [ntfy/{why}] {title} :: {message}")
        if image:
            print(f"  [ntfy/{why}] billede: {image}")
        return

    # JSON-publicering frem for headers: ntfy-headers skal være ASCII, og
    # titlerne her indeholder både æ/ø/å og é.
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": tags or [],
    }
    if click:
        payload["click"] = click
    if image:
        # icon giver det lille ikon, attach den store forhåndsvisning.
        payload["icon"] = image
        payload["attach"] = image

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        server, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        print(f"  [ntfy] sendt: {title}")
        print(f"  [ntfy] topic-fingeraftryk: {topic_fingerprint(topic)} "
              f"(længde {len(topic)}), server {server}")
    except Exception as exc:
        # En fejlet notifikation må aldrig vælte kørslen: så ville vi også miste
        # de øvrige mål og den state-opdatering der forhindrer gentagne beskeder.
        print(f"  [ntfy] FEJL ved afsendelse: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            print("state.json kunne ikke læses, starter forfra", file=sys.stderr)
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def should_send_diagnostic(entry: dict, now: datetime) -> bool:
    last = entry.get("last_diagnostic")
    if not last:
        return True
    try:
        previous = datetime.fromisoformat(last)
    except ValueError:
        return True
    return now - previous >= timedelta(hours=DIAG_COOLDOWN_HOURS)


# --------------------------------------------------------------------------
# Tjek
# --------------------------------------------------------------------------

def check_product(target: dict, state: dict, now: datetime, dry_run: bool,
                  fallback_image: str | None = None) -> None:
    key, name, url = target["key"], target["name"], target["url"]
    entry = state.setdefault(key, {})
    print(f"[produkt] {name}")

    html, error = http_get(url)
    if html is None:
        print(f"  hentning mislykkedes: {error}")
        if should_send_diagnostic(entry, now):
            entry["last_diagnostic"] = now.isoformat()
            notify(
                f"Overvågning fejler: {name}",
                f"Siden kunne ikke hentes ({error}). Lagerstatus er ukendt indtil "
                f"det virker igen. Næste diagnose tidligst om {DIAG_COOLDOWN_HOURS} timer.",
                priority=2, tags=["warning"], click=url, dry_run=dry_run,
            )
        return

    image = target.get("image") or detect_image(html, url) or fallback_image
    status, method, evidence = detect_status(html)
    previous = entry.get("status", UNKNOWN)
    entry["status"] = status
    entry["method"] = method
    entry["url"] = url
    print(f"  status={status} (metode={method}) forrige={previous}")
    print(f"  belæg: {evidence[:200]}")

    if status == UNKNOWN:
        if should_send_diagnostic(entry, now):
            entry["last_diagnostic"] = now.isoformat()
            notify(
                f"Overvågning kan ikke aflæse: {name}",
                "Siden blev hentet, men ingen af metoderne kunne aflæse lagerstatus. "
                "Sandsynligvis er sidens opbygning ændret, og overvågningen skal "
                "justeres. Tjek selv linket indtil da.",
                priority=2, tags=["warning"], click=url, dry_run=dry_run,
            )
        return

    if status == IN_STOCK and previous != IN_STOCK:
        confidence = "" if method in ("json-ld", "embedded-json") else \
            f"\n\n(Aflæst via mindre sikker metode: {method}. Tjek selv siden.)"
        notify(
            f"PÅ LAGER: {name}",
            f"Varen er netop blevet tilgængelig.{confidence}\n\n{url}",
            priority=5, tags=["package", "tada"], click=url, image=image,
            dry_run=dry_run,
        )
    elif status == OUT_OF_STOCK and previous == IN_STOCK:
        print("  gik fra på lager til udsolgt (ingen notifikation)")


LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
ROBOTS_SITEMAP_RE = re.compile(r"(?im)^\s*Sitemap:\s*(\S+)")

# Sidste sti-segment på en produktside er varens id. Bilka, BR og føtex bruger
# rene tal (/produkter/<slug>/200392202/), salling.dk bruger p-foran
# (/boern/toej/<slug>/p-1180431/). Segmentet før er i begge tilfælde slug'en,
# altså et læsbart produktnavn.
ID_SEGMENT_RE = re.compile(r"^(?:p-)?\d{4,}$")

# Sitemappene fylder flere megabyte. De hentes derfor sjældent: de er en
# opdagelsesmekanisme, ikke en lagermåling, og nye varer dukker alligevel først
# op i takt med at butikken genopbygger dem.
DISCOVERY_INTERVAL_HOURS = 11


def url_segments(url: str):
    return [part for part in urllib.parse.urlparse(url).path.split("/") if part]


def is_product_url(url: str) -> bool:
    """Peger URL'en på en vare frem for en kategori- eller indholdsside?"""
    parts = url_segments(url)
    return bool(parts) and bool(ID_SEGMENT_RE.match(parts[-1]))


def product_slug(url: str) -> str:
    """Varens læsbare navn fra URL'en, uden kategoristien.

    Der matches på slug'en alene og ikke på hele URL'en, fordi kategorinavne
    ellers kan udløse falske træf: salling.dk har hundredvis af hudpleje-varer
    med "booster" i stien.
    """
    parts = url_segments(url)
    if not parts:
        return ""
    if ID_SEGMENT_RE.match(parts[-1]) and len(parts) > 1:
        return parts[-2]
    return parts[-1]


def _collect_locs(body: str, seen: set, depth: int = 0):
    """URLer fra et sitemap, og fra dets undersitemaps hvis det er et index."""
    locs = LOC_RE.findall(body)
    if "<sitemapindex" not in body[:600].lower():
        return locs
    if depth >= 2:
        return []
    out = []
    for child in locs:
        if child in seen:
            continue
        seen.add(child)
        child_body, _ = http_get(child)
        if child_body:
            out.extend(_collect_locs(child_body, seen, depth + 1))
    return out


def sitemap_product_urls(site: str):
    """Alle produkt-URLer fra et sites sitemap. Returnerer (urls, fejl).

    Sitemappets placering slås op i robots.txt, som er standardmåden at
    annoncere den på. Koncernens sider ligger ikke på samme platform:
    bilka/br/foetex bruger /sitemap/sitemap-index.xml, mens netto og salling
    bruger /sitemap.xml. En hardkodet sti ville kun virke det ene sted.
    """
    robots, _ = http_get(site + "/robots.txt")
    candidates = ROBOTS_SITEMAP_RE.findall(robots) if robots else []
    candidates += [site + "/sitemap/sitemap-index.xml", site + "/sitemap.xml"]

    seen, last_error = set(), "intet sitemap kunne læses"
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        body, error = http_get(candidate)
        if body is None:
            last_error = f"{candidate}: {error}"
            continue
        urls = _collect_locs(body, seen)
        if urls:
            return [u for u in urls if is_product_url(u)], None
        last_error = f"{candidate}: ingen URLer i sitemappet"
    return None, last_error


def check_discovery(target: dict, state: dict, now: datetime, dry_run: bool,
                    fallback_image: str | None = None,
                    already_watched: set | None = None) -> None:
    """Opdag nye varer der matcher et mønster, via sitemap.

    Listesiderne bygges af JavaScript og kan ikke læses uden en browser.
    Sitemappet er derimod udgivet netop så crawlere må læse det, dækker hele
    katalogets varer i stedet for én kategoriside, og indeholder slugs der er
    læsbare produktnavne. Fundne varer lægges i overvågning, så deres
    lagerstatus derefter følges som enhver anden vare.
    """
    key, name = target["key"], target["name"]
    pattern = re.compile(target["match"], re.IGNORECASE)
    # Tilbehør bærer varens navn: en akrylkasse til en booster box hedder
    # "booster box" i slug'en. Uden frasortering ville hver opbevaringsæske
    # udløse alarm, og falske alarmer lærer en at ignorere de ægte.
    exclude = re.compile(target["exclude"], re.IGNORECASE) if target.get("exclude") else None
    entry = state.setdefault(key, {})
    known = entry.setdefault("known_urls", {})
    print(f"[opdagelse] {name}")

    last = entry.get("last_run")
    if last and not target.get("always"):
        try:
            elapsed = now - datetime.fromisoformat(last)
            if elapsed < timedelta(hours=DISCOVERY_INTERVAL_HOURS):
                hours = elapsed.total_seconds() / 3600
                print(f"  sidst kørt for {hours:.1f} timer siden, springer over")
                return
        except ValueError:
            pass

    watched = state.setdefault("watched_products", {})
    # Elite Trainer Box'en står allerede som fast mål og matcher også
    # 30th-mønsteret. Uden dette ville den blive opdaget som ny og derefter
    # tjekket to gange, med dobbelte notifikationer den dag den lander.
    configured = already_watched or set()
    total = 0
    for site in target["sites"]:
        urls, error = sitemap_product_urls(site)
        if urls is None:
            print(f"  {site}: {error}")
            if should_send_diagnostic(entry, now):
                entry["last_diagnostic"] = now.isoformat()
                notify(
                    f"Opdagelse fejler: {name}",
                    f"Sitemap for {site} kunne ikke læses ({error}).",
                    priority=2, tags=["warning"], dry_run=dry_run,
                )
            continue
        total += len(urls)
        matches, filtered = [], 0
        for url in urls:
            label = product_slug(url).replace("-", " ")
            if not pattern.search(label):
                continue
            if exclude and exclude.search(label):
                filtered += 1
                continue
            matches.append(url)
        note = f", {filtered} frasorteret som tilbehør" if filtered else ""
        print(f"  {site}: {len(urls)} produkter, {len(matches)} matcher{note}")

        for url in matches:
            if url in known or url in configured:
                continue
            label = product_slug(url).replace("-", " ") or url
            known[url] = {"first_seen": now.isoformat(), "name": label}
            # Læg varen i lagerovervågning, så vi også fanger at den kommer
            # på lager, ikke kun at den er oprettet.
            watched[url] = {"name": label.title(), "url": url}
            print(f"  NY: {label}")
            notify(
                f"BOOSTER BUNDLE FUNDET: {label.title()}",
                f"Ny vare i katalog: {label}.\n\nDen er nu også lagerovervåget."
                f"\n\n{url}",
                priority=5, tags=["package", "tada"], click=url,
                image=target.get("image") or fallback_image, dry_run=dry_run,
            )

    entry["last_run"] = now.isoformat()
    entry["products_scanned"] = total
    print(f"  {len(known)} kendte match, {total} produkter gennemgået")


# --------------------------------------------------------------------------
# Tidsvindue
# --------------------------------------------------------------------------

def slot_is_active(now: datetime) -> bool:
    """Dag (06-20 dansk tid): hvert slot. Nat: kun toppen af timen.

    Cron i GitHub Actions kører kun i UTC og kender ikke dansk sommertid.
    Derfor fyrer cron'en hvert 30. minut hele døgnet, og vinduet afgøres her,
    hvor vi kan regne i Europe/Copenhagen og ramme rigtigt året rundt.
    """
    if 6 <= now.hour < 20:
        return True
    return now.minute < 15


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="tjek uanset tidsvindue")
    parser.add_argument("--dry-run", action="store_true",
                        help="skriv notifikationer til konsollen i stedet for ntfy")
    parser.add_argument("--probe", metavar="URL",
                        help="hent én URL og udskriv hvad kaskaden finder")
    parser.add_argument("--test-notify", action="store_true",
                        help="send én testbesked og afslut, uden at røre state")
    args = parser.parse_args()

    if args.test_notify:
        # Bevis at hele vejen til telefonen virker. Uden den kan en forkert
        # secret først vise sig den dag varen rent faktisk kommer på lager.
        #
        # Billedet hentes fra en rigtig produktside frem for at være hardkodet,
        # så testen dækker hele kæden: hentning, aflæsning af schema.org-data,
        # billedudtræk og vedhæftning. Et fast billede ville kun bevise det
        # sidste led.
        targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
        image = targets.get("default_image") or None
        source = "standardbillede fra targets.json" if image else "intet"
        link = "https://github.com/ekkelund/pokemon-stock-watch/actions"

        products = targets.get("products") or []
        if products:
            first = products[0]
            html, error = http_get(first["url"])
            if html is None:
                print(f"  kunne ikke hente {first['name']}: {error}")
            else:
                found = detect_image(html, first["url"])
                if found:
                    image, source, link = found, first["name"], first["url"]
                else:
                    print(f"  intet billede fundet på {first['name']}")

        print(f"  billedkilde: {source}")
        print(f"  billede: {image}")
        notify(
            "Test fra lagerovervågningen",
            f"Virker denne besked, når de rigtige også frem.\n\n"
            f"Billedet er hentet live fra produktsiden ({source}), "
            f"så det er sådan en rigtig notifikation kommer til at se ud.\n\n"
            f"Ingen varer er kommet på lager; dette er kun en test.",
            priority=3, tags=["white_check_mark"], click=link,
            image=image, dry_run=args.dry_run,
        )
        return 0

    if args.probe:
        html, error = http_get(args.probe)
        if html is None:
            print(f"hentning mislykkedes: {error}")
            return 1
        print(f"hentet {len(html)} tegn")
        print("lagerstatus:", detect_status(html))
        print("billede:", detect_image(html, args.probe))
        links = sorted(set(PRODUCT_HREF_RE.findall(html)))
        print(f"produktlinks: {len(links)}")
        for slug, product_id in links[:40]:
            print(f"  {product_id}  {slug}")
        return 0

    now = datetime.now(TZ)
    if not (args.force or slot_is_active(now)):
        print(f"{now:%Y-%m-%d %H:%M %Z}: uden for tjek-vinduet, springer over")
        return 0

    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    state = load_state()
    print(f"Tjek kl. {now:%Y-%m-%d %H:%M %Z}\n")

    fallback_image = targets.get("default_image") or None

    for target in targets.get("products", []):
        try:
            check_product(target, state, now, args.dry_run, fallback_image)
        except Exception as exc:  # ét dødt mål må ikke stoppe resten
            print(f"  uventet fejl: {type(exc).__name__}: {exc}", file=sys.stderr)
        print()

    # Varer opdaget via sitemap følges på lige fod med de konfigurerede.
    for url, info in sorted(state.get("watched_products", {}).items()):
        target = {"key": f"fundet:{url}", "name": info.get("name", url), "url": url}
        try:
            check_product(target, state, now, args.dry_run, fallback_image)
        except Exception as exc:
            print(f"  uventet fejl: {type(exc).__name__}: {exc}", file=sys.stderr)
        print()

    configured_urls = {t["url"] for t in targets.get("products", [])}
    for target in targets.get("discovery", []):
        try:
            check_discovery(target, state, now, args.dry_run, fallback_image,
                            configured_urls)
        except Exception as exc:
            print(f"  uventet fejl: {type(exc).__name__}: {exc}", file=sys.stderr)
        print()

    # Dagsstempel frem for tidsstempel: state.json ændrer sig så højst én gang
    # i døgnet når intet sker. Det giver én commit om dagen, hvilket både er
    # et let livstegn og nok aktivitet til at GitHub ikke slår cron'en fra
    # (planlagte workflows deaktiveres efter 60 dage uden aktivitet).
    state["_heartbeat"] = f"{now:%Y-%m-%d}"
    if not args.dry_run:
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
