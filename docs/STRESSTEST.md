# Stresstest av Ordverk

Kört den 20 september 2026 på Ubuntu 26.04, ARM64, Python 3.14, GTK 4.22 och
libadwaita 1.9. GTK kördes under Xvfb med mjukvarurendering. Tiderna är lokala
mätvärden, inte prestandalöften för andra datorer.

## Resultat och åtgärder

Slutkörningen omfattade **100 000 JSON-strängar** och **5 000 strängar vardera**
i PO, Qt TS, XLIFF 1.2 och XLIFF 2.0. Alla fem filer importerades rekursivt
genom programmets vanliga mappimport. Tvåhundra ändringar per fil sparades och
lästes tillbaka med exakt jämförelse av översättningarna. Testet provade även
markering av alla strängar, den sista strängen, 100 redigeringar, tolv sökningar,
100 snabba strängbyten, avbrytning och tillämpning av 1 000 batchförslag.
En manuell ändring som gjordes efter batchförberedelsen bevarades.

**Inga assertionsfel, ohanterade GTK-återanropsfel eller felaktigt bevarade
översättningar observerades.** Följande problem hittades och åtgärdades:

- Snabba strängbyten kunde köa onödiga minnessökningar och fördröja nästa jobb.
  Sökningar som ännu inte startat avbryts när en ny sträng väljs.
- Batchjobb kopierade tidigare hela katalogen före avbrytningskontrollen.
  Avbrytning kontrolleras först och löpande; endast enheter som ryms inom
  körningens gräns kopieras.
- Förloppsuppdateringar skapade var sitt GTK-köobjekt. Nu sparas endast den
  senaste uppdateringen och visas av programmets befintliga timer.
- Ett redan visat AI-förslag kunde bli inaktuellt efter en manuell ändring.
  Förslaget rensas då och tillämpningen kontrollerar strängens revision igen.
- Kontroll av osparade ändringar skannade tidigare hela filen vid varje tangenttryck
  och strängbyte. En fortfarande ändrad post ger nu ett snabbt, kontrollerat svar.
  Ångrade ändringar och ersatta poster kontrolleras på nytt.

Jämförelsen nedan gjordes med 50 000 JSON-strängar och samma övriga format:

| Moment | Före | Efter |
| --- | ---: | ---: |
| Nästa bakgrundsjobb efter 100 strängbyten | 0,414 s | 0,024 s |
| Batchjobb som redan avbrutits | 0,577 s | < 0,001 s |
| Ta fram 1 000 lokala batchförslag | 0,486 s | 0,023 s |

## Slutkörning med 100 000 JSON-strängar

| Moment | Tid |
| --- | ---: |
| Importera mappen med samtliga fem filer | 1,803 s |
| Läsa / spara och verifiera JSON-filen | 0,396 / 1,267 s |
| Markera alla 100 000 strängar | 0,177 s |
| Tolv sökningar/filterbyten sammanlagt | 9,758 s |
| Hundra snabba strängbyten sammanlagt | 0,021 s |
| Nästa bakgrundsjobb efter strängbytena | 0,024 s |
| Hantera 100 000 förloppsuppdateringar | 0,028 s |
| Ta fram / tillämpa 1 000 lokala batchförslag | 0,064 / 0,002 s |

Högsta uppmätta residenta minnesanvändning var **943,5 MiB** för hela
testprocessen, som samtidigt höll både testdata, katalogkopior och GTK-modeller.
Största uppmätta mellanrum mellan huvudloopens pulser var **2,171 sekunder**.
Testet skickar bland annat 100 redigeringar i en sammanhängande serie utan
paus mellan dem. Stora filer kan fortfarande ge en märkbar paus när listan byggs om.
Batchens minnesträffar och kvalitetsresultat simulerades för att isolera
köhantering, avbrytning och dataskydd. Detta mäter inte externa API-tider
eller fullständig språkgranskning av 100 000 strängar.

## Återskapa

Efter installation med utvecklingsberoenden:

```sh
GDK_BACKEND=x11 GSK_RENDERER=cairo GTK_A11Y=test LIBGL_ALWAYS_SOFTWARE=1 \
  xvfb-run -a dbus-run-session -- .venv/bin/python tests/stress_app.py \
  --size 100000 --output /tmp/ordverk-stress.json
```

Testet använder en tillfällig arbetskatalog och cache. Det ändrar inga egna
översättningsfiler och gör inga externa API-anrop. CI kör samma test med
5 000 JSON-strängar utöver de vanliga format-, integrations- och GTK-testerna.
