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


def detect_image(html: str, base_url: str):
    """Find produktets billede, så notifikationen viser den faktiske vare.

    JSON-LD foretrækkes: "image" på et Product er varen selv, mens og:image
    på en listeside lige så godt kan være butikkens logo.
    """
    for entry in parse_ld_json(html):
        for _path, key, value in walk_json(entry):
            if key.lower() == "image" and isinstance(value, str) and value.strip():
                return urllib.parse.urljoin(base_url, value.strip())
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
    for entry in parse_ld_json(html):
        for path, key, value in walk_json(entry):
            if key.lower() != "availability":
                continue
            status = normalise_availability(value)
            if status:
                return status, "json-ld", f"{path} = {value}"
    return None


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
# Produkter på en liste-/søgeside
# --------------------------------------------------------------------------

PRODUCT_HREF_RE = re.compile(r'/produkter/([a-z0-9\-]+)/(\d+)/?', re.IGNORECASE)


def find_products(html: str, pattern: re.Pattern):
    """Find produkter på en listeside hvis navn eller URL-slug matcher.

    Slug'en i /produkter/<slug>/<id>/ er selv et læsbart produktnavn, så den
    er et robust match-grundlag også når JSON-strukturen ikke kan læses.
    """
    found = {}

    def remember(product_id, name, slug):
        product_id = str(product_id)
        haystack = f"{name} {slug}".replace("-", " ")
        if not pattern.search(haystack):
            return
        existing = found.get(product_id)
        # Foretræk et rigtigt navn frem for et slug-udledt.
        if existing and existing["source"] == "name" and not name:
            return
        found[product_id] = {
            "id": product_id,
            "name": (name or slug.replace("-", " ")).strip(),
            "slug": slug,
            "source": "name" if name else "slug",
        }

    for entry in parse_ld_json(html):
        for path, key, value in walk_json(entry):
            if key.lower() == "name" and isinstance(value, str):
                match = PRODUCT_HREF_RE.search(json.dumps(entry))
                if match:
                    remember(match.group(2), value, match.group(1))

    data = parse_next_data(html)
    if data is not None:
        for path, key, value in walk_json(data):
            if key.lower() in {"name", "title", "displayname"} and isinstance(value, str):
                remember(_nearby_id(data, path) or value, value, "")

    for slug, product_id in PRODUCT_HREF_RE.findall(html):
        remember(product_id, "", slug)

    return sorted(found.values(), key=lambda p: p["id"])


def _nearby_id(root, path: str):
    """Find et id-felt i samme objekt som en navne-sti."""
    parent_path = path.rsplit(".", 1)[0]
    node = root
    for step in re.findall(r"[^.\[\]]+", parent_path):
        try:
            node = node[int(step)] if step.isdigit() else node[step]
        except Exception:
            return None
    if isinstance(node, dict):
        for key in ("id", "productId", "sku", "code", "itemNumber"):
            if key in node and isinstance(node[key], (str, int)):
                return node[key]
    return None


# --------------------------------------------------------------------------
# ntfy
# --------------------------------------------------------------------------

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


def check_listing(target: dict, state: dict, now: datetime, dry_run: bool,
                  fallback_image: str | None = None) -> None:
    key, name, url = target["key"], target["name"], target["url"]
    pattern = re.compile(target.get("match", "booster.{0,15}bundle"), re.IGNORECASE)
    entry = state.setdefault(key, {})
    known = entry.setdefault("known_products", {})
    print(f"[liste] {name}")

    html, error = http_get(url)
    if html is None:
        print(f"  hentning mislykkedes: {error}")
        if should_send_diagnostic(entry, now):
            entry["last_diagnostic"] = now.isoformat()
            notify(
                f"Overvågning fejler: {name}",
                f"Siden kunne ikke hentes ({error}).",
                priority=2, tags=["warning"], click=url, dry_run=dry_run,
            )
        return

    total_links = len(set(PRODUCT_HREF_RE.findall(html)))
    matches = find_products(html, pattern)
    print(f"  {total_links} produktlinks på siden, {len(matches)} matcher mønsteret")

    # Nul produktlinks overhovedet betyder at siden ikke blev læst som forventet
    # (JavaScript-renderet, bot-blokeret, omlagt). Det er ikke det samme som
    # "ingen booster bundles", og må ikke passere i stilhed.
    if total_links == 0:
        if should_send_diagnostic(entry, now):
            entry["last_diagnostic"] = now.isoformat()
            notify(
                f"Overvågning kan ikke aflæse: {name}",
                "Siden blev hentet, men der blev ikke fundet ét eneste produktlink. "
                "Listen bliver sandsynligvis bygget af JavaScript, eller siden er "
                "lagt om. Overvågningen skal justeres.",
                priority=2, tags=["warning"], click=url, dry_run=dry_run,
            )
        return

    entry["products_seen"] = total_links
    for product in matches:
        product_id = product["id"]
        known_entry = known.get(product_id)
        if known_entry is None:
            known[product_id] = {"name": product["name"], "first_seen": now.isoformat()}
            notify(
                f"BOOSTER BUNDLE: {product['name']}",
                f"Nyt produkt dukket op på {name}.\n\n{url}",
                priority=5, tags=["package", "tada"], click=url,
                image=target.get("image") or fallback_image, dry_run=dry_run,
            )
            print(f"  NY: {product['id']} {product['name']}")
        else:
            known_entry.setdefault("name", product["name"])
            print(f"  kendt: {product['id']} {product['name']}")


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
    args = parser.parse_args()

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

    for target in targets.get("listings", []):
        try:
            check_listing(target, state, now, args.dry_run, fallback_image)
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
