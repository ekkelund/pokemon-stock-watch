#!/usr/bin/env python3
"""Test af udtrækningskaskaden mod syntetiske sider.

De rigtige sider kan ikke nås fra udviklingsmiljøet, så disse tests låser
logikken fast: at hver metode i kaskaden virker isoleret, at rækkefølgen er
den tiltænkte, og at en uforståelig side giver "unknown" i stedet for at blive
gættet til "udsolgt".
"""
import re
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from check import (  # noqa: E402
    IN_STOCK, OUT_OF_STOCK, UNKNOWN,
    LOC_RE, PRODUCT_HREF_RE, detect_image, detect_status, is_product_url,
    product_slug, slot_is_active, visible_text,
)
from datetime import datetime  # noqa: E402


def page(body: str) -> str:
    return f"<!doctype html><html><head></head><body>{body}</body></html>"


LDJSON_IN = page('''
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
 "name":"Pokémon Elite Trainer Box 30th",
 "offers":{"@type":"Offer","price":"349.95","priceCurrency":"DKK",
           "availability":"https://schema.org/InStock"}}
</script>
<div>Udsolgt</div>
''')

LDJSON_OUT = LDJSON_IN.replace("InStock", "OutOfStock")

NEXT_DATA_IN = page('''
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"product":{"id":"200392202","name":"ETB 30th",
 "stock":{"inStock":true,"stockLevel":4}}}}}
</script>
''')

NEXT_DATA_OUT = page('''
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"product":{"id":"200392202","name":"ETB 30th",
 "stock":{"inStock":false,"stockLevel":0},"soldOut":true}}}}
</script>
''')

APP_ROUTER_IN = page(r'''
<script>self.__next_f.push([1,"a:[\"$\",\"div\",null,{\"availability\":\"InStock\"}]\n"])</script>
''')

TEXT_OUT_PAGE = page('<h1>Pokémon ETB</h1><button disabled>Læg i kurv</button>'
                     '<span>Midlertidigt udsolgt</span>')
TEXT_IN_PAGE = page('<h1>Pokémon ETB</h1><button>Læg i kurv</button><span>På lager</span>')
OPAQUE_PAGE = page('<div id="root"></div>')

LISTING = page('''
<a href="/produkter/pokemon-booster-bundle-scarlet-violet/200111222/">Vis</a>
<a href="/produkter/pokemon-elite-trainer-box-30th-samlekort/200392202/">Vis</a>
<a href="/produkter/pokemon-booster-pakke-enkelt/200333444/">Vis</a>
''')


class TestStatus(unittest.TestCase):
    def test_ldjson_wins_over_stray_text(self):
        # Siden indeholder ordet "Udsolgt" i en anden sammenhæng. JSON-LD
        # ligger før tekst i kaskaden og skal afgøre sagen.
        status, method, _ = detect_status(LDJSON_IN)
        self.assertEqual(status, IN_STOCK)
        self.assertEqual(method, "json-ld")

    def test_any_available_variant_means_in_stock(self):
        # salling.dk har en offer pr. størrelse. Er én på lager, kan varen
        # købes, selv om de første i listen er udsolgt.
        html = page('''<script type="application/ld+json">
        {"@type":"Product","name":"T-shirt","offers":[
          {"@type":"Offer","availability":"https://schema.org/OutOfStock"},
          {"@type":"Offer","availability":"https://schema.org/LimitedAvailability"}]}
        </script>''')
        status, method, evidence = detect_status(html)
        self.assertEqual(status, IN_STOCK)
        self.assertEqual(method, "json-ld")
        self.assertIn("2 varianter", evidence)

    def test_all_variants_sold_out(self):
        html = page('''<script type="application/ld+json">
        {"@type":"Product","offers":[
          {"@type":"Offer","availability":"https://schema.org/OutOfStock"},
          {"@type":"Offer","availability":"https://schema.org/SoldOut"}]}
        </script>''')
        self.assertEqual(detect_status(html)[0], OUT_OF_STOCK)

    def test_limited_availability_counts_as_in_stock(self):
        html = page('''<script type="application/ld+json">{"@type":"Product",
        "offers":{"availability":"https://schema.org/LimitedAvailability"}}</script>''')
        self.assertEqual(detect_status(html)[0], IN_STOCK)

    def test_ldjson_out_of_stock(self):
        self.assertEqual(detect_status(LDJSON_OUT)[0], OUT_OF_STOCK)

    def test_embedded_json_in_stock(self):
        status, method, _ = detect_status(NEXT_DATA_IN)
        self.assertEqual(status, IN_STOCK)
        self.assertEqual(method, "embedded-json")

    def test_embedded_json_out_of_stock(self):
        self.assertEqual(detect_status(NEXT_DATA_OUT)[0], OUT_OF_STOCK)

    def test_app_router_stream(self):
        status, method, _ = detect_status(APP_ROUTER_IN)
        self.assertEqual(status, IN_STOCK)
        self.assertEqual(method, "script-text")

    def test_text_out_beats_disabled_button(self):
        status, method, evidence = detect_status(TEXT_OUT_PAGE)
        self.assertEqual(status, OUT_OF_STOCK)
        self.assertEqual(method, "text")
        self.assertEqual(evidence, "midlertidigt udsolgt")

    def test_text_in(self):
        self.assertEqual(detect_status(TEXT_IN_PAGE)[0], IN_STOCK)

    def test_opaque_page_is_unknown_not_out_of_stock(self):
        # Det vigtigste enkeltkrav: en side vi ikke forstår må aldrig blive
        # læst som "udsolgt", for så ville vi aldrig notificere igen.
        self.assertEqual(detect_status(OPAQUE_PAGE)[0], UNKNOWN)

    def test_scripts_excluded_from_visible_text(self):
        self.assertNotIn("udsolgt", visible_text(
            page('<script>var x = "Udsolgt";</script><p>På lager</p>')).lower())


class TestImage(unittest.TestCase):
    BASE = "https://www.bilka.dk/produkter/x/1/"

    def test_prefers_ldjson_product_image(self):
        html = page('''<script type="application/ld+json">{"@type":"Product","image":"https://cdn.example/vare.jpg"}</script><meta property="og:image" content="https://cdn.example/logo.png">''')
        self.assertEqual(detect_image(html, self.BASE), "https://cdn.example/vare.jpg")

    def test_image_list_beats_og_image(self):
        # Salling leverer image som en liste. Læses den ikke, falder vi tilbage
        # på og:image, som er en bred 1.9:1-beskæring til sociale medier.
        html = page(
            '''<script type="application/ld+json">{"@type":"Product",'''
            '''"image":["https://cdn.example/vare-1.jpg","https://cdn.example/vare-2.jpg"]}'''
            '''</script><meta property="og:image" content="https://cdn.example/social.jpg?ar=1.9:1">'''
        )
        self.assertEqual(detect_image(html, self.BASE), "https://cdn.example/vare-1.jpg")

    def test_image_object_with_url(self):
        html = page(
            '''<script type="application/ld+json">{"@type":"Product",'''
            '''"image":{"@type":"ImageObject","url":"https://cdn.example/obj.jpg"}}</script>'''
        )
        self.assertEqual(detect_image(html, self.BASE), "https://cdn.example/obj.jpg")

    def test_image_nested_under_graph(self):
        html = page(
            '''<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},'''
            '''{"@type":"Product","image":["https://cdn.example/graf.jpg"]}]}</script>'''
        )
        self.assertEqual(detect_image(html, self.BASE), "https://cdn.example/graf.jpg")

    def test_falls_back_to_og_image(self):
        html = page('<meta property="og:image" content="https://cdn.example/logo.png">')
        self.assertEqual(detect_image(html, self.BASE), "https://cdn.example/logo.png")

    def test_relative_url_is_made_absolute(self):
        html = page('<meta property="og:image" content="/media/vare.jpg">')
        self.assertEqual(detect_image(html, self.BASE), "https://www.bilka.dk/media/vare.jpg")

    def test_none_when_no_image(self):
        self.assertIsNone(detect_image(page("<p>ingen billeder</p>"), self.BASE))


class TestDiscovery(unittest.TestCase):
    """Opdagelse sker via sitemap, ikke via kategorisiderne.

    Kategorisiderne bygges af JavaScript og indeholder ingen produktlinks i
    HTML'en; sitemappet er udgivet til crawlere og dækker hele kataloget.
    """

    PATTERN = re.compile("booster.{0,15}bundle", re.IGNORECASE)
    SITEMAP = """<?xml version="1.0"?><urlset>
      <url><loc>https://www.br.dk/produkter/pokemon-booster-bundle-mega/200555666/</loc></url>
      <url><loc>https://www.br.dk/produkter/pokemon-elite-trainer-box-30th-samlekort/200392202/</loc></url>
      <url><loc>https://www.br.dk/produkter/panini-hot-wheel-samlekort-booster-pakke/200315819/</loc></url>
      <url><loc>https://www.br.dk/c/legetoej/</loc></url>
    </urlset>"""

    def product_urls(self):
        return [u for u in LOC_RE.findall(self.SITEMAP) if "/produkter/" in u]

    def test_extracts_only_product_urls(self):
        urls = self.product_urls()
        self.assertEqual(len(urls), 3)
        self.assertNotIn("https://www.br.dk/c/legetoej/", urls)

    def test_matches_bundle_only(self):
        # Bindestreger gøres til mellemrum, så mønsteret læser slug'en som navn.
        hits = [u for u in self.product_urls()
                if self.PATTERN.search(u.replace("-", " "))]
        self.assertEqual(len(hits), 1)
        self.assertIn("200555666", hits[0])

    def test_loose_booster_is_not_a_bundle(self):
        loose = "https://www.br.dk/produkter/panini-hot-wheel-samlekort-booster-pakke/200315819/"
        self.assertIsNone(self.PATTERN.search(loose.replace("-", " ")))

    def test_slug_and_id_are_recoverable(self):
        match = PRODUCT_HREF_RE.search(self.product_urls()[0])
        self.assertEqual(match.group(2), "200555666")
        self.assertEqual(match.group(1), "pokemon-booster-bundle-mega")


class TestUrlShapes(unittest.TestCase):
    """De fem sites deler ikke URL-form.

    bilka/br/foetex: /produkter/<slug>/<tal>/
    salling:         /<kategorier>/<slug>/p-<tal>/
    netto:           ingen varer overhovedet
    """

    BILKA = "https://www.bilka.dk/produkter/pokemon-booster-bundle-mega/200555666/"
    SALLING = ("https://salling.dk/boern/toej/t-shirts/"
               "mile-pokemon-t-shirt-bright-white-116-cm/p-1180431/")
    SALLING_BEAUTY = ("https://salling.dk/skoenhed/haar/haarpleje/"
                      "booster-serum-100-ml/p-384325/")
    NETTO = "https://netto.dk/butikker/netto-aarhus-c/"

    def test_recognises_product_urls(self):
        self.assertTrue(is_product_url(self.BILKA))
        self.assertTrue(is_product_url(self.SALLING))

    def test_rejects_non_product_urls(self):
        self.assertFalse(is_product_url(self.NETTO))
        self.assertFalse(is_product_url("https://www.bilka.dk/c/legetoej/"))

    def test_slug_excludes_category_path(self):
        self.assertEqual(product_slug(self.BILKA), "pokemon-booster-bundle-mega")
        self.assertEqual(product_slug(self.SALLING),
                         "mile-pokemon-t-shirt-bright-white-116-cm")

    def test_category_path_cannot_cause_false_match(self):
        # Hele URL'en indeholder både "booster" og et kategoriord, men slug'en
        # er det eneste der må tælle. Ellers ville hudpleje udløse alarmer.
        pattern = re.compile("booster.{0,15}bundle", re.IGNORECASE)
        self.assertIsNone(
            pattern.search(product_slug(self.SALLING_BEAUTY).replace("-", " ")))

    def test_bundle_in_slug_still_matches(self):
        pattern = re.compile("booster.{0,15}bundle", re.IGNORECASE)
        self.assertIsNotNone(
            pattern.search(product_slug(self.BILKA).replace("-", " ")))


class Test30thPattern(unittest.TestCase):
    """Mønsteret mod rigtige slugs fra katalogerne.

    Alle strengene herunder er faktiske varer målt 25. september 2026. Et bart
    30th-mønster gav 15 træf hos føtex, hvoraf kun 5 var Pokémon. Derfor kræves
    begge led.
    """

    MATCH = re.compile(r"(?=.*pokemon)(?=.*(?:30th|30 aar|30 ars|celebration))",
                       re.IGNORECASE)

    def hit(self, slug):
        return bool(self.MATCH.search(slug.replace("-", " ")))

    def test_pokemon_30th_products(self):
        for slug in [
            "pokemon-elite-trainer-box-30th-samlekort",
            "pokemon-sylveon-ex-box-30th",
            "pokemon-30th-samlekort",
            "pokemon-poster-collection-30th",
            "pokemon-binder-collection-30th",
        ]:
            self.assertTrue(self.hit(slug), slug)

    def test_other_brands_anniversaries_are_ignored(self):
        # Alle sammen rigtige varer der matchede et bart 30th-mønster.
        for slug in [
            "lego-ninjago-x-1-ninjabil-15-aars-jubilaeum-71867",
            "original-tamagotchi-30-aars-jubilaeum",
            "switch-rayman-30th-anniversary-edition",
            "ps5-rayman-30th-anniversary-edition",
            "cd-kandis-35-aars-jubilaeumsalbum",
            "paw-patrol-figurer-all-paws-celebration-gaveaeske",
            "jumbo-tegnestue-jubilaeum-puslespil-1000-brikker",
        ]:
            self.assertFalse(self.hit(slug), slug)

    def test_pokemon_without_30th_is_ignored(self):
        # Fundet af det tidligere, for brede mønster. Ægte Pokémon-varer, men
        # ikke 30th, og derfor ikke det vi jagter.
        for slug in [
            "pokemon-pitch-black-checklane-booster-pack",
            "pokemon-tcg-booster-pack-samlekort",
            "pokemon-greninja-ex-tin-samlekort",
            "mile-pokemon-t-shirt-bright-white-116-cm",
        ]:
            self.assertFalse(self.hit(slug), slug)


class TestSchedule(unittest.TestCase):
    def test_daytime_both_slots(self):
        self.assertTrue(slot_is_active(datetime(2026, 9, 25, 9, 0)))
        self.assertTrue(slot_is_active(datetime(2026, 9, 25, 9, 30)))

    def test_night_only_top_of_hour(self):
        self.assertTrue(slot_is_active(datetime(2026, 9, 25, 23, 0)))
        self.assertFalse(slot_is_active(datetime(2026, 9, 25, 23, 30)))
        self.assertTrue(slot_is_active(datetime(2026, 9, 25, 3, 0)))
        self.assertFalse(slot_is_active(datetime(2026, 9, 25, 3, 30)))

    def test_window_boundaries(self):
        self.assertTrue(slot_is_active(datetime(2026, 9, 25, 6, 30)))   # dag starter
        self.assertFalse(slot_is_active(datetime(2026, 9, 25, 20, 30)))  # nat starter
        self.assertTrue(slot_is_active(datetime(2026, 9, 25, 20, 0)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
