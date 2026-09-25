# Lagerovervågning: Pokémon-samlekort hos Salling Group

Tjekker bilka.dk, foetex.dk og br.dk og sender en ntfy-notifikation når
noget bliver tilgængeligt. Kører som GitHub Actions-cron, så der skal ikke
stå en maskine tændt derhjemme.

## Hvad der overvåges

**Elite Trainer Box 30th** (vare 200392202) på alle tre sider. Notifikation
når varen skifter fra utilgængelig til på lager. Alle tre sider tjekkes, fordi
Salling ikke nødvendigvis frigiver lager samtidig på dem.

**Booster bundles** på tre oversigtssider. Notifikation når et produkt hvis
navn eller URL matcher `booster.{0,15}bundle` dukker op for første gang.

Mål redigeres i `watcher/targets.json`.

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

1. `json-ld` - schema.org `offers.availability`. Mest pålidelig.
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

**Det er endnu ikke bekræftet mod de rigtige sider.** Overvågningen er udviklet
i et miljø uden adgang til bilka.dk, foetex.dk og br.dk, så kaskaden er testet
mod syntetiske sider, ikke mod Sallings faktiske markup. To ting kan derfor vise
sig ved første rigtige kørsel:

- Listerne bygges måske udelukkende af JavaScript, så der ikke står produktlinks
  i HTML'en. Så melder overvågningen "kan ikke aflæse".
- Salling kan afvise trafik fra datacenter-IP'er. Så melder den HTTP 403.

Begge dele rapporterer sig selv via ntfy frem for at fejle i stilhed. Kør
`python3 watcher/check.py --probe <url>` for at se præcis hvad en given side
giver, og justér derfra.

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
```
