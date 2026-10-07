# Werkstuur — 2.2.0

Werkstuur ondersteunt de intake en voorbereiding van technisch servicewerk.
De cloudversie gebruikt de bestaande gratis Render-service en Supabase voor organisatiegegevens, accounts en private bestanden.

## Werkomgevingen

- De software-eigenaar beheert klantbedrijven, softwarestatus en support.
- Elk klantbedrijf heeft een eigen bedrijfsbeheerder, planners en monteurs.
- De bedrijfsbeheerder beheert het eigen team; planners beheren klanten en dossiers.
- Monteurs zien hun toegewezen dossiers en eigen supportmeldingen.
- Klanten vullen een publieke intake in via de unieke link van hun servicebedrijf.
- Supportinzage vereist voorafgaande goedkeuring door een eigen actieve bedrijfsbeheerder. De eigenaar vraagt met reden 15, 30 of 60 minuten aan; de beheerder kan een kortere duur kiezen, afwijzen of direct intrekken. Een aanvraag vervalt na 24 uur, inzage uiterlijk na 60 minuten vanaf goedkeuring.
- Alleen de goedgekeurde aanvrager kan de werkruimte en bijlagen bekijken. Wijzigingen, accountovername, intake-tokens, volledige exports en back-updownloads vallen buiten de toestemming. Elke API-aanvraag controleert de toestemming opnieuw.
- Aanvragen, besluiten, openen en verlaten worden in een aparte alleen-toevoegen geschiedenis bijgehouden en met de organisatie geback-upt. Beheerders vinden aanvragen en geschiedenis onder Ondersteuning; er worden geen e-mails verstuurd voor dit proces.

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

De server leest zijn geheimen uit de omgeving. Gebruik de bestaande configuratie in `render.yaml`; commit nooit sleutels of persoonlijke exports. De Supabase secret key blijft uitsluitend op de server. Pas bij bestaande installaties eerst `support_access_setup.sql` als bijgehouden migratie toe; deze voegt de tabellen, server-only toestemmingsfuncties en aangepaste snapshotfunctie toe. `operations_setup.sql` bevat het server-only back-upregister en de snapshotfuncties. De dagelijkse planning gebruikt `OPERATIONS_TOKEN` in serverconfiguratie en Vault; de activering staat in `BACKUP_AUTOMATION_ENABLED`. Geen extra betaalde dienst is nodig.

## Controleren

```sh
python3 -m unittest discover -q
node test_editor_refinements.js
node test_workspace_quality.js
node test_support_access.js
node --check command-v210.js
```

De tests gebruiken geïsoleerde rollen en fictieve gegevens, leveren geen e-mails af en wijzigen geen productiegegevens. Test met echte klantgebruikers en een fysieke telefoon voordat een klantpilot start. De pilot is gericht op 4–6 weken met 20–50 dossiers en gemeten plannerstijd, first-time-fix en tweede bezoeken.

## Wijzigingen 2.1.2

De publieke intake vereist naam, plaats en minimaal één geldig contactgegeven. Alle velden en foto-aanvragen worden vóór dossieraanmaak op de server gecontroleerd. De actuele klantomgeving blijft bij supportinzage zichtbaar, met een directe terugkeer vanuit softwarebeheer. Oudere zonnepanelendossiers worden opnieuw beoordeeld met hun daadwerkelijk bewaarde waarnemingen; niet-analyseerbare dossiers tonen geen oud advies als actuele beoordeling en kunnen door de eigen planner worden hersteld.

De gratis Render-dienst kan na inactiviteit ongeveer een minuut nodig hebben om te starten. Dit abonnement biedt geen productiegarantie. Er is geen betaalde upgrade uitgevoerd.

## Wijzigingen 2.2.0

Klantgestuurde, tijdelijke toestemming vervangt de onbeperkte eigenaarinzage. Alleen een bedrijfsbeheerder die zelf lid is van het bedrijf en geen platformeigenaar is kan toestemming geven. Intrekken, verlopen, pauzeren van de organisatie of verlies van de goedkeurende beheerderstoegang sluit de API. De browser verwijdert de actieve inzage en keert terug naar Werkstuur Control. Herstel van een back-up mag toestemmingen nooit opnieuw activeren; de meegenomen toestemmingsgegevens zijn geschiedenis.
