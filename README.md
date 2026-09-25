# Lagerovervågning: Pokémon-samlekort hos Salling Group

Tjekker bilka.dk, foetex.dk og br.dk og sender en ntfy-notifikation når
noget bliver tilgængeligt. Kører som GitHub Actions-cron, så der skal ikke
stå en maskine tændt derhjemme.

> Leder du efter forklaringen uden teknik? Se **[TIL-THEO.md](TIL-THEO.md)**.

## Hvad der overvåges

**Elite Trainer Box 30th** (vare 200392202) på alle tre sider. Notifikation
når varen skifter fra utilgængelig til på lager. Alle tre sider tjekkes, fordi
Salling ikke nødvendigvis frigiver lager samtidig på dem.

**Pokémon 30th** opdages via sitemap. Notifikation når en ny 30th-vare dukker
op i kataloget, og varen lægges derefter automatisk i lagerovervågning, så du
også får besked når den kan købes.

Mønsteret kræver **både** `pokemon` og et 30th-mærke i varens slug:

```
(?=.*pokemon)(?=.*(?:30th|30 aar|30 ars|celebration))
```

Begge led er nødvendige, og det blev målt frem for gættet. Et bart
30th-mønster gav 15 træf hos føtex, hvoraf kun 5 var Pokémon:

| Fanges | Frasorteres |
|---|---|
| `pokemon-elite-trainer-box-30th-samlekort` | `lego-ninjago-15-aars-jubilaeum` |
| `pokemon-sylveon-ex-box-30th` | `original-tamagotchi-30-aars-jubilaeum` |
| `pokemon-30th-samlekort` | `switch-rayman-30th-anniversary-edition` |
| `pokemon-poster-collection-30th` | `cd-kandis-35-aars-jubilaeumsalbum` |
| `pokemon-binder-collection-30th` | `paw-patrol-all-paws-celebration-gaveaeske` |

Omvendt ville et bart `pokemon`-mønster fange hvert eneste løse boosterbrev og
hver Pokémon-t-shirt hos salling.dk.

Varer der allerede står som faste mål under `products` springes over i
opdagelsen. Ellers ville Elite Trainer Box'en, som selv matcher mønsteret,
blive tjekket to gange og udløse dobbelte notifikationer den dag den lander.

### De fem sites

Koncernen har fem webadresser, men de er ikke ens. Målt 25. september 2026:

| Site | Varer i sitemap | Samlekort | Med i overvågning |
|---|---|---|---|
| bilka.dk | 56.456 | ja | ja |
| foetex.dk | 39.642 | ja | ja |
| br.dk | 16.848 | ja | ja |
| salling.dk | 261.768 | nej | ja, som beredskab |
| netto.dk | 0 | nej | ja, som snubletråd |

De tre første er der hvor det sker. De har samlekort, kender vare 200392202,
og kører den Nuxt-platform hvor produktsider leverer schema.org JSON-LD.

**salling.dk er et modehus** på en helt anden platform. De 39 Pokémon-fund er
t-shirts og shorts, og de 44 booster-fund er hårserum og ansigtscreme. Der er
ingen samlekort i dag. Det er taget med alligevel, fordi det faktisk *er* en
webshop og sortimentet kan ændre sig.

**netto.dk er ikke en webshop.** Sitemappet rummer 686 sider, hvoraf 584 er
butiksadresser og 43 er opskrifter. Nul varer. Det er taget med som en billig
snubletråd: skulle Netto åbne en shop, dukker varerne op i sitemappet, og så
fanger overvågningen det. Det koster 686 URLer hver 11. time, altså intet.

Fordi de to platforme har forskellige URL-former, matches der på varens **slug
alene** og ikke på hele URL'en:

```
bilka:   /produkter/pokemon-booster-bundle-mega/200555666/
salling: /skoenhed/haar/haarpleje/booster-serum-100-ml/p-384325/
                                  ^^^^^^^ kun dette led tæller
```

Uden den afgrænsning ville salling.dk's hudplejekategori kunne udløse falske
alarmer. Sitemappets placering slås op i `robots.txt` frem for at være
hardkodet, netop fordi de to platforme lægger den forskellige steder.

### Hvorfor sitemap og ikke kategorisiderne

De oprindelige mål var tre oversigtssider. Første rigtige kørsel viste at de
ikke indeholder ét eneste produktlink i HTML'en: siderne kører Nuxt, og
produktgitteret hentes først efter hydrering. Uden en browser er der intet at
læse.

Sitemappet løser det bedre end en browser ville:

- Det er udgivet netop så crawlere må læse det, og `robots.txt` peger på det.
- Det dækker hele katalogets varer, ikke kun én kategoriside. Havde vi holdt
  os til kategorisiderne, ville en bundle placeret i en anden kategori aldrig
  blive fundet.
- Slug'en i `/produkter/<slug>/<id>/` er et læsbart produktnavn, så matchet
  kan ske direkte på URL'en.

føtex-søgesiden er droppet som mål: `robots.txt` har `Disallow: /search`.
Sitemappet dækker føtex alligevel.

Sitemappene fylder flere megabyte, så opdagelsen kører hver 11. time i stedet
for hver kørsel. Lagerovervågningen af konkrete varer kører fuld kadence, og
det er den der er tidskritisk.

## Kadence

Cron'en fyrer hvert 30. minut, men `check.py` afgør selv om slottet skal
bruges, målt i `Europe/Copenhagen`:

| Tidsrum (dansk tid) | Tjek |
|---|---|
| 06:00 - 20:00 | hvert 30. minut |
| 20:00 - 06:00 | hver time |

Grunden til at vinduet ligger i scriptet og ikke i cron-udtrykket: GitHub
Actions' cron kører udelukkende i UTC og kender ikke dansk sommertid. Hardkodede
UTC-timer ville forskyde vinduet en time to gange om året. Scriptet regner i
rigtig lokaltid og rammer derfor korrekt hele året. De ekstra no-op-kørsler om
natten koster ingenting, fordi Actions-minutter er gratis på offentlige repos.

## Slutdato

Jagten stopper **1. december 2026**. Sidste dag der tjekkes er 30. november.

Datoen står som `watch_until` i `watcher/targets.json` frem for at afhænge af
at nogen husker at slukke. På dagen sendes én afskedsbesked og derefter
ingenting. Beskeden er ikke pynt: en overvågning der bare holder op med at
sige noget, ser præcis ud som en der er gået i stykker, og det er en dårlig
måde at finde ud af at man ikke længere bliver advaret.

Vil du fortsætte, så ret datoen eller ryd feltet. Vil du stoppe helt, så slå
workflowet fra under **Actions → Lagerovervågning → ⋯ → Disable workflow**,
så det ikke kører forgæves hver halve time.

## Opsætning

1. **Tilføj ntfy-topic som secret.** Settings → Secrets and variables →
   Actions → New repository secret, navn `NTFY_TOPIC`.
   Topicet må ikke stå i koden: repoet er offentligt, og på ntfy.sh kan
   enhver der kender topic-navnet både læse og skrive til det.

2. **Abonnér i ntfy-appen** på samme topic.

3. **Kør en test.** Actions → Lagerovervågning → Run workflow.
   Sæt `dry_run` til true for at se hvad der ville blive sendt uden at sende det.

Valgfrit: `NTFY_SERVER` som repository *variable* hvis du kører selvhostet ntfy,
og `NTFY_TOKEN` som secret hvis serveren kræver adgangstoken.

## Billede i notifikationen

Notifikationer vedhæfter et billede, så du kan se hvad der er kommet ind uden
at åbne linket. Billedet vælges i denne rækkefølge:

1. `image` sat direkte på målet i `targets.json`
2. varens eget billede aflæst fra siden (schema.org `image`, ellers `og:image`)
3. `default_image` fra `targets.json`

Punkt 2 gør at produktnotifikationer får det rigtige billede helt af sig selv.
`default_image` er kun til listesiderne, hvor der ikke hentes en produktside.
Den skal være en offentligt tilgængelig URL; upload billedet til `assets/` i
dette repo og peg på raw-URL'en.

## Om pålideligheden

Sallings sider er Next.js-drevne, og deres interne datastruktur er hverken
dokumenteret eller stabil. Lagerstatus aflæses derfor gennem en kaskade, fra
mest til mindst pålidelig:

1. `json-ld` - schema.org `offers.availability`. Mest pålidelig, og den der
   faktisk bruges: alle tre sider leverer korrekt JSON-LD på produktsider.
2. `embedded-json` - `__NEXT_DATA__` gennemsøgt for lager-agtige nøgler.
3. `script-text` - nøgle/værdi-par fundet i App Router-payloads.
4. `text` - danske vendinger som "Udsolgt" og "Læg i kurv". Lav tiltro:
   en knap kan stå i DOM'en selv når den er slået fra. Notifikationer aflæst
   på denne måde bærer et forbehold i selve beskeden.

Kan ingen af metoderne aflæse siden, bliver status `unknown`, og du får en
lavprioritets-diagnose i stedet for tavshed. Det er et bevidst valg: en scraper
der stille rapporterer "udsolgt" for evigt fordi markuppen er ændret, er værre
end en der siger højt at den er gået i stykker. Samme gælder listesiderne, hvor
nul fundne produktlinks behandles som en fejl og ikke som "ingen bundles".
Diagnoser sendes højst hver 12. time pr. mål, så en vedvarende fejl ikke spammer.

**Produktsiderne er bekræftet mod de rigtige sider.** Alle tre leverer
schema.org JSON-LD med korrekt `availability`, og Salling afviser ikke
GitHub-runnernes IP-adresser. Lagerdelen hviler altså på den mest pålidelige
metode i kaskaden, ikke på tekstgætteri.

De øvrige trin i kaskaden er sikkerhedsnet, testet mod syntetiske sider. Skulle
Salling holde op med at levere JSON-LD, træder de til, og notifikationen bærer
da et forbehold om hvilken metode der blev brugt.

## Tilstand

`watcher/state.json` commit'es tilbage til repoet af workflowet. Den husker
sidst kendte status pr. mål, så en notifikation kun sendes på selve overgangen
og ikke hvert 30. minut så længe varen er på lager.

Filen indeholder også et `_heartbeat` med dags-granularitet. Det giver én commit
i døgnet selv når intet sker, hvilket både er et synligt livstegn og nok
aktivitet til at GitHub ikke slår cron'en fra (planlagte workflows deaktiveres
automatisk efter 60 dage uden aktivitet i repoet).

## Kommandoer

```bash
python3 watcher/check.py --force --dry-run   # tjek nu, send intet
python3 watcher/check.py --probe <url>       # vis hvad kaskaden finder på én side
python3 watcher/test_check.py                # kør testene
python3 watcher/diagnose.py <url>            # udskriv en sides opbygning
python3 watcher/diagnose_sitemap.py          # tæl produkter og match i sitemappene
```

De to diagnoseværktøjer kan også køres fra fanen Actions, hvilket er nyttigt
når man undersøger fra en maskine uden adgang til domænerne.
