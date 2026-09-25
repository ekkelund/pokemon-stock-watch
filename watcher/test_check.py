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
    detect_status, find_products, slot_is_active, visible_text,
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


class TestListing(unittest.TestCase):
    def setUp(self):
        self.pattern = re.compile("booster.{0,15}bundle", re.IGNORECASE)

    def test_matches_bundle_via_slug(self):
        found = find_products(LISTING, self.pattern)
        self.assertEqual([p["id"] for p in found], ["200111222"])
        self.assertIn("booster bundle", found[0]["name"])

    def test_ignores_non_bundle_products(self):
        ids = [p["id"] for p in find_products(LISTING, self.pattern)]
        self.assertNotIn("200392202", ids)  # elite trainer box
        self.assertNotIn("200333444", ids)  # løs booster-pakke, ikke bundle

    def test_no_products_on_empty_page(self):
        self.assertEqual(find_products(OPAQUE_PAGE, self.pattern), [])


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
