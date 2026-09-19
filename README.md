# EVN Smart Meter – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/HA-2026.1%2B-blue.svg)](https://www.home-assistant.io/)

Inoffizielle Home Assistant Integration für das [EVN / Netz Niederösterreich Smart Meter Portal](https://smartmeter.netz-noe.at/).

Importiert Stromverbrauchsdaten deines Smart Meters als **externe Statistiken** in Home Assistant – kompatibel mit dem **Energy Dashboard**.

## Features

- 📊 **Externer Statistik-Import** – stündliche Verbrauchsdaten (kWh) als HA-Statistik (`evn_smartmeter:consumption`), kompatibel mit dem Energy Dashboard
- 📅 **Monatsverbrauch** – Verbrauch des laufenden Monats als Sensor (kWh), berechnet aus den gespeicherten Statistiken
- 🕕 **Täglicher Fetch im Zeitfenster** – zu einer zufälligen Minute zwischen 05:00 und 07:00, per Optionen einstellbar
- 🔄 **Sofortiger Fetch bei Start und Reload** – Daten werden auch beim Neuladen der Integration abgerufen
- ♻️ **Automatische Wiederholung** – bei Verbindungsfehlern oder noch nicht veröffentlichten Vortagsdaten bis zu zweimal im Abstand von 30 Minuten
- 📆 **Vollständige Historie beim ersten Import** – danach werden nur noch neue Tage geholt
- 🔐 Login über Username/Password des Smart Meter Portals, mit erneuter Anmeldung bei abgelehnten Zugangsdaten
- 👥 **Mehrere Konten** – jedes Konto bekommt seine eigene Statistik

## Installation (HACS)

1. **HACS** öffnen → Integrationen → ⋮ (drei Punkte oben rechts) → **Benutzerdefinierte Repositories**
2. URL eingeben: `https://github.com/infabo/ha-evn-smartmeter`
3. Kategorie: **Integration** → Hinzufügen
4. „EVN Smart Meter" suchen und installieren
5. **Home Assistant neu starten**

## Einrichtung

1. **Einstellungen** → **Geräte & Dienste** → **Integration hinzufügen**
2. Nach „EVN Smart Meter" suchen
3. Zugangsdaten für [smartmeter.netz-noe.at](https://smartmeter.netz-noe.at/) eingeben
4. Fertig! Die Sensoren werden automatisch erstellt und der erste Datenimport startet sofort.

Der erste Import holt die gesamte im Portal verfügbare Historie, Monat für Monat rückwärts. Je nach Alter des Zählers dauert das einige Minuten.

### Optionen

Unter **Konfigurieren** lässt sich das Zeitfenster für den täglichen Abruf einstellen. Der genaue Zeitpunkt wird innerhalb des Fensters zufällig gewählt, damit nicht alle Installationen gleichzeitig auf das Portal zugreifen. Voreinstellung ist 05:00 bis 07:00.

### Dienst `evn_smartmeter.reset_statistics`

Löscht die gespeicherten Statistiken und importiert die vollständige Historie neu. Nützlich, wenn die kumulative Summe durch Datenlücken verfälscht wurde.

## Energy Dashboard

1. **Einstellungen** → **Dashboards** → **Energie**
2. Unter „Stromnetz" → **Verbrauch hinzufügen**
3. Statistik `evn_smartmeter:consumption` auswählen
4. Speichern

Bei mehreren Konten trägt das zuerst eingerichtete Konto die ID `evn_smartmeter:consumption`, jedes weitere `evn_smartmeter:consumption_<benutzername>`.

## Sensoren & Statistiken

| Name | Typ | Beschreibung | Einheit |
|------|-----|-------------|---------|
| EVN Smart Meter Import | Sensor | Zeigt den Import-Status (`Imported`, `No data`, `Login error`, `Connection error`, `Error`) | – |
| EVN Smart Meter Monthly Consumption | Sensor | Verbrauch des laufenden Monats | kWh |
| evn_smartmeter:consumption | Externe Statistik | Stündliche Verbrauchsdaten für das Energy Dashboard | kWh |

Beide Sensoren hängen an einem Gerät pro Konto und lassen sich damit einem Bereich zuordnen.

## Architektur

Die Integration folgt dem Muster der [enelgrid](https://github.com/sathia-musso/enelgrid) Integration:

- Die 15-Minuten-Intervalle der EVN API werden zu **stündlichen** Werten aggregiert (HA-Statistiken erfordern Top-of-Hour-Timestamps)
- Die Stundenstempel werden als verstrichene Zeit ab lokaler Mitternacht berechnet, damit die Zeitumstellung keine doppelten oder fehlenden Stunden erzeugt
- Daten werden als **externe Statistiken** gespeichert (`async_add_external_statistics`), nicht als Entity-States
- Der Import-Sensor (`should_poll = False`) triggert keine automatischen Updates – nur der Timer, der Start und der Reset-Dienst lösen Fetches aus
- Der erste Import holt die gesamte Historie; danach wird ab dem letzten gespeicherten Statistikwert fortgeschrieben (Upsert – vorhandene Stunden werden aktualisiert)
- Die kumulative Summe wird aus dem letzten gespeicherten Wert vor dem Importfenster fortgeschrieben, auch wenn davor Tage fehlen

## Hinweise

- Die Daten stammen von der API des Smart Meter Portals und sind typischerweise am nächsten Morgen verfügbar. Fehlen die Vortagswerte beim Abruf noch, wiederholt die Integration den Versuch.
- Der erste Fetch nach Installation importiert die gesamte verfügbare Historie.
- Die Integration nutzt einen vendored [PyNoeSmartmeter](https://github.com/Xlinx64/PyNoeSmartmeter) Client und benötigt keine zusätzlichen Python-Pakete.

## Credits

- [PyNoeSmartmeter](https://github.com/Xlinx64/PyNoeSmartmeter) von David Illichmann – Python-Wrapper für die Netz NÖ API
- [enelgrid](https://github.com/sathia-musso/enelgrid) – Referenz-Integration für das Architektur-Pattern
- [EVN Smartmeter Wrapper](https://www.lteforum.at/mobilfunk/evn-smartmeter-api-wrapper-influx-importer-grafana-dashboard.21319/) von A.E.I.O.U.

## Lizenz

MIT License

## Disclaimer

Dies ist kein offizielles Produkt von EVN oder Netz Niederösterreich GmbH. Nutzung auf eigene Verantwortung.
