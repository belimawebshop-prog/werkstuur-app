# Werkstuur: gegevens en bestanden herstellen

Een beheerder kan in Productinstellingen **Complete backup downloaden** gebruiken.
De private ZIP bevat de organisatiegegevens, dossiers, notities, uitkomsten,
gebruikersmetadata, instellingen, auditgegevens en alle bijlagebestanden van
die organisatie. `manifest.json` bevat grootte en SHA-256-controlewaarden.
De download stopt als een bestand ontbreekt, onvolledig is of de grens van
64 MB / 250 bijlagen wordt overschreden. Er ontstaat dan geen gedeeltelijke
backup die zich als compleet presenteert.

Bewaar de ZIP privé. De export bevat contactgegevens en de intakekoppeling.
Wachtwoordhashes, aanmeldsessies en servergeheimen worden niet geëxporteerd.
De organisatie-export is geen export van het volledige Werkstuur-platform.

## Herstelproef zonder productie te wijzigen

Gebruik de standaard Python-bibliotheek; er zijn geen betaalde diensten nodig:

```sh
python3 backup_archive.py werkstuur-backup.zip --restore-to nieuwe-herstelmap
```

De controle valideert eerst de organisatie, dossierverwijzingen, het manifest,
alle bestandscontrolesommen en veilige bestandspaden. Alleen daarna worden
gegevens en bijlagen in een **nieuwe** lokale map teruggezet. Een bestaande
map wordt geweigerd. De map krijgt permissie 700, de bestanden 600.
Dit programma maakt geen verbinding met de database en wijzigt geen accounts.

Voor werkelijk productieherstel: houd de huidige backup beschikbaar, controleer
de herstelde organisatiegegevens, leg een import/mapping voor de juiste
database vast en controleer alle private opslagpaden voordat de import wordt
uitgevoerd. Herstel van wachtwoorden of serverconfiguratie staat hier los van.
Gebruik hiervoor bestaande account-herstelprocessen en serverinstellingen;
zet geen nieuwe wachtwoorden, sessies of sleutels in een gewone gegevensbackup.

## Applicatie terugzetten

De broncode staat in Git. Voor de verfijning was de stabiele commit
`6be9470d6a290a3c16671d90ea475b2b1110c1e3` live. De bestaande Render-publicatie
`dep-db1pompsrm7s73cpfi9g` blijft beschikbaar als eerdere publicatie. Een
terugzetactie op de eigen portaalservice verandert `app.werkstuur.nl` niet.

## Controle voor een echte klant

Maak de werkelijke klantorganisatie en haar bestaande gebruikers afzonderlijk
gereed. Manuel houdt zijn Werkstuur-eigenaarsrol. De klantdirecteur/beheerder,
planner en monteurs horen in hun eigen klantorganisatie. Voer daarna met die
echte klantgebruikers de intake-, planning-, toewijzings- en afrondingsronde
uit. De automatische HTTP-proef gebruikt uitsluitend geïsoleerde testrollen;
zij vervangt geen echte klantsessie of een controle op een fysiek mobiel toestel.
