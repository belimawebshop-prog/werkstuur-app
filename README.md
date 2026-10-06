# Werkstuur — 2.1.1

Werkstuur ondersteunt de intake en voorbereiding van technisch servicewerk.
De cloudversie gebruikt de bestaande gratis Render-service en Supabase voor organisatiegegevens, accounts en private bestanden.

## Werkomgevingen

- De software-eigenaar beheert klantbedrijven, softwarestatus en support.
- Elk klantbedrijf heeft een eigen bedrijfsbeheerder, planners en monteurs.
- De bedrijfsbeheerder beheert het eigen team; planners beheren klanten en dossiers.
- Monteurs zien hun toegewezen dossiers en eigen supportmeldingen.
- Klanten vullen een publieke intake in via de unieke link van hun servicebedrijf.
- Supportinzage van de software-eigenaar is alleen lezen binnen het klantbedrijf.

## Gegevens en herstel

Dagelijkse private back-ups bevatten de organisatiegegevens en alle bijlagen. Elke opgeslagen kopie wordt teruggelezen en volledig gecontroleerd. De laatste zeven geslaagde versies zijn beschikbaar. Wachtwoordhashes, sessies en servergeheimen zijn uitgesloten. Zie [HERSTEL.md](HERSTEL.md) voor de grenzen, herstelproef en aanvullende eigen private download.

De eigenaar ziet een back-upoverzicht; bedrijfsbeheerders zien hun eigen kopieën en een feitelijke controle van hun pilotinrichting. De inrichtingcontrole is geen goedkeuring van privacyafspraken en vervangt geen praktijktest.

## Slimme analyse

De voorbereidingsanalyse gebruikt vastgelegde waarnemingen, kennisprofielen voor ondersteunde merken en conservatieve regels. Dit is geen aangesloten generatief taalmodel, automatische fotoanalyse of directe uitlezing van apparaten. De planner en vakbekwame monteur beoordelen het advies. De toepassing voert geen veiligheidskritische apparaatacties uit.

## Cloud starten

Python 3.12+, vervolgens:

```sh
pip install -r requirements.txt
python cloud_server.py
```

De server leest zijn geheimen uit de omgeving. Gebruik de bestaande configuratie in `render.yaml`; commit nooit sleutels of persoonlijke exports. De Supabase secret key blijft uitsluitend op de server. `operations_setup.sql` bevat het server-only back-upregister en de snapshotfuncties. De dagelijkse planning gebruikt `OPERATIONS_TOKEN` in serverconfiguratie en Vault; de activering staat in `BACKUP_AUTOMATION_ENABLED`. Geen extra betaalde dienst is nodig.

## Controleren

```sh
python3 -m unittest discover -q
node test_editor_refinements.js
node --check command-v210.js
```

De tests gebruiken geïsoleerde rollen en fictieve gegevens, leveren geen e-mails af en wijzigen geen productiegegevens. Test met echte klantgebruikers en een fysieke telefoon voordat een klantpilot start. De pilot is gericht op 4–6 weken met 20–50 dossiers en gemeten plannerstijd, first-time-fix en tweede bezoeken.
