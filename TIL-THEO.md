# Hej Theo

Det her er en robot der jagter Pokémon-kort for os. Den sover aldrig.

Her er hvordan den virker, og hvorfor den er lidt smartere end den lyder.

## Problemet

Elite Trainer Box 30th er udsolgt overalt. Når butikkerne får nye ind, er de
væk igen på ingen tid, fordi alle andre også sidder og venter.

Man kan ikke sidde og trykke F5 på bilka.dk i døgndrift. Men det kan en
computer.

## Ideen

Jeg har bygget et program der gør præcis det, bare 48 gange i døgnet, i fem
butikker på én gang, uden nogensinde at blive træt eller glemme det.

Butikkerne er **Bilka, føtex, BR, Salling og Netto**. De hører alle sammen til
det samme firma, Salling Group, så de får tit varer ind samtidig. Men ikke
altid, og derfor tjekker vi alle fem.

## Sådan virker den

**1. Den henter siden**

Præcis som din browser gør når du går ind på bilka.dk. Den lader bare som om
den er en Chrome-browser, så butikken ikke synes det er mærkeligt.

**2. Den leder efter en skjult besked i siden**

Det her er det fede. Alle webshops gemmer en lille hemmelig seddel i deres
sider, som er beregnet til Google. Den ser sådan her ud:

```json
{
  "@type": "Product",
  "name": "Pokémon Elite Trainer Box 30th samlekort",
  "offers": {
    "price": 349.95,
    "availability": "https://schema.org/OutOfStock"
  }
}
```

Kan du se det sidste ord? `OutOfStock`. Det betyder udsolgt.

Den dag der står `InStock` i stedet, ved robotten at varen er kommet, uden at
skulle gætte ud fra hvordan siden ser ud. Det er meget mere sikkert end at
kigge efter om knappen "Læg i kurv" er grå eller grøn.

**3. Den sender en besked til min telefon**

Med billede af pakken, prisen, og hvilken butik der har den. Og et link der
går direkte derhen.

## Det smarteste trick

Først prøvede jeg at lade robotten kigge på butikkernes oversigtssider, dem
hvor alle samlekortene står i et gitter.

Det virkede overhovedet ikke. Den fandt nul varer.

Grunden er at de sider bliver bygget af JavaScript **efter** siden er hentet.
Det er ligesom at få tilsendt en tom opskrift og en pose mel: browseren bager
kagen, men robotten fik kun posen.

Så jeg fandt på noget bedre. Alle store hjemmesider har en **sitemap**, som er
en kæmpe liste over hver eneste side de har. Den er lavet til at Google må
læse den, den står direkte i deres `robots.txt`, og den er bare almindelig
tekst.

Nu læser robotten den i stedet. Tre fordele:

- Den ser **hele** butikken, ikke kun én kategori. Ligger en ny Pokémon-vare i
  en anden afdeling, finder vi den alligevel.
- Adressen på varen indeholder selve navnet:
  `/produkter/pokemon-elite-trainer-box-30th-samlekort/200392202/`
  Så vi kan genkende varer uden overhovedet at åbne siden.
- Det er tilladt. Vi sniger os ikke ind nogen steder.

Den gennemgår **241.956 varer** på cirka 20 sekunder.

## Hvad den jagter

Kun 30th. Lige nu to ting:

| Vare | Nummer |
|---|---|
| Pokémon Elite Trainer Box 30th | 200392202 |
| Pokémon 30th Samlekort | 200392214 |

Dukker der en **ny** 30th-vare op i en af butikkerne, opdager robotten det
selv og begynder at holde øje med den også. Den skal ikke have besked.

## Hvorfor det var svært at ramme rigtigt

Jeg kunne ikke bare lede efter ordet "booster". Så havde vi fået beskeder om:

- Panini fodboldkort-boostere
- Hot Wheels-boostere
- **hårserum** og **ansigtscreme** (salling.dk har over hundrede varer med
  "booster" i navnet, fordi det åbenbart hedder det i hudpleje)
- en autostol til børn der hedder "booster seat"

Og jeg kunne heller ikke bare lede efter "30th". Så havde vi fået:

- LEGO Ninjago 15-års jubilæum
- Tamagotchi 30-års jubilæum
- Rayman 30th Anniversary til PS5
- et Kandis-album fra deres 35-års jubilæum

Så robotten kræver **begge dele**: ordet skal være Pokémon-noget **og**
30th-noget. Så rammer den kun det rigtige.

Der var også en fælde jeg var lige ved at gå i. BR sælger en akrylkasse som
man kan opbevare en booster box i. Den hedder selv "booster box" i navnet.
Uden en ekstra regel ville vi have fået alarm hver gang nogen solgte en tom
plastikkasse.

## Når noget går i stykker

Det vigtigste ved sådan en robot er ikke at den virker. Det er at den **siger
det højt når den ikke virker**.

Forestil dig at Bilka laver deres hjemmeside om, og robotten ikke længere kan
læse den. Hvis den bare sagde "udsolgt" hver gang, ville vi tro alt var fint,
og vi ville aldrig nogensinde få besked igen. Vi ville sidde og vente på et
pling der aldrig kom.

Derfor er den bygget til at indrømme når den er i tvivl. Kan den ikke aflæse
en side, sender den en besked om at den er gået i stykker i stedet for at
gætte. Det er kedeligt at få, men meget bedre end stilhed.

## Tal

| | |
|---|---|
| Butikker | 5 |
| Varer gennemsøgt hver gang | 241.956 |
| Tid det tager | ca. 20 sekunder |
| Tjek i dagtimerne | hvert 30. minut |
| Tjek om natten | hver time |
| Tjek om året | ca. 16.000 |
| Hvad det koster | 0 kr. |

Det sidste er fordi GitHub lader computere køre gratis for åbne projekter.

## Vil du ændre noget?

Alt der styrer jagten står i filen `watcher/targets.json`. Der kan man:

- tilføje flere varer der skal holdes øje med
- ændre hvad robotten leder efter
- fjerne ting vi ikke gider (vi har allerede sagt nej til plakater og mapper)

Vil du se den arbejde, så gå ind under fanen **Actions** heroppe. Der står hver
eneste gang den har kørt, og hvad den fandt.

Resten af koden ligger i `watcher/check.py`. Den er skrevet i Python, og der er
skrevet kommentarer undervejs der forklarer hvorfor tingene er som de er. Hvis
du har lyst til at kigge, så start med funktionen `detect_status`. Det er den
der finder ud af om noget er på lager.

## Og så planen

Når det plinger, køber vi alt hvad vi kan nå.

Du er med på holdet. Hold telefonen tændt. 💰
