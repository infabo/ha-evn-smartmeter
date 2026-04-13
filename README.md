# EVN Smart Meter – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/HA-2026.1%2B-blue.svg)](https://www.home-assistant.io/)

Inoffizielle Home Assistant Integration für das [EVN / Netz Niederösterreich Smart Meter Portal](https://smartmeter.netz-noe.at/).

Importiert Stromverbrauchsdaten deines Smart Meters als **externe Statistiken** in Home Assistant – kompatibel mit dem **Energy Dashboard**.

## Features

- 📊 **Externer Statistik-Import** – stündliche Verbrauchsdaten (kWh) als HA-Statistik (`evn_smartmeter:consumption`), kompatibel mit dem Energy Dashboard
- 📅 **Monatsverbrauch** – kumulativer Verbrauch des aktuellen Monats als Sensor (kWh)
- 🔄 **Täglicher Fetch um 06:00** – automatisch via `async_track_time_change`
- 🔄 **Sofortiger Fetch bei Reload** – Daten werden auch beim Neuladen der Integration abgerufen
- 🔐 Login über Username/Password des Smart Meter Portals
- 📆 **7-Tage-Lookback** – importiert die letzten 7 Tage bei jedem Fetch

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

## Energy Dashboard

1. **Einstellungen** → **Dashboards** → **Energie**
2. Unter „Stromnetz" → **Verbrauch hinzufügen**
3. Statistik `evn_smartmeter:consumption` auswählen
4. Speichern

## Sensoren & Statistiken

| Name | Typ | Beschreibung | Einheit |
|------|-----|-------------|---------|
| EVN Smart Meter Import | Sensor | Zeigt den Import-Status (`Imported`, `No data`, `Error`, …) | – |
| EVN Smart Meter Monthly Consumption | Sensor | Kumulativer Verbrauch des aktuellen Monats | kWh |
| evn_smartmeter:consumption | Externe Statistik | Stündliche Verbrauchsdaten für das Energy Dashboard | kWh |

## Architektur

Die Integration folgt dem Muster der [enelgrid](https://github.com/sathia-musso/enelgrid) Integration:

- Die 15-Minuten-Intervalle der EVN API werden zu **stündlichen** Werten aggregiert (HA-Statistiken erfordern Top-of-Hour-Timestamps)
- Daten werden als **externe Statistiken** gespeichert (`async_add_external_statistics`), nicht als Entity-States
- Der Import-Sensor (`should_poll = False`) triggert keine automatischen Updates – nur der tägliche Timer und Reload lösen Fetches aus
- Bei jedem Fetch werden die letzten 7 Tage importiert (Upsert – vorhandene Stunden werden aktualisiert)

## Hinweise

- Die Daten stammen von der API des Smart Meter Portals und sind typischerweise am nächsten Morgen verfügbar.
- Der erste Fetch nach Installation importiert sofort die letzten 7 Tage.
- Die Integration nutzt einen vendored [PyNoeSmartmeter](https://github.com/Xlinx64/PyNoeSmartmeter) Client.

## Credits

- [PyNoeSmartmeter](https://github.com/Xlinx64/PyNoeSmartmeter) von David Illichmann – Python-Wrapper für die Netz NÖ API
- [enelgrid](https://github.com/sathia-musso/enelgrid) – Referenz-Integration für das Architektur-Pattern
- [EVN Smartmeter Wrapper](https://www.lteforum.at/mobilfunk/evn-smartmeter-api-wrapper-influx-importer-grafana-dashboard.21319/) von A.E.I.O.U.

## Lizenz

MIT License

## Disclaimer

Dies ist kein offizielles Produkt von EVN oder Netz Niederösterreich GmbH. Nutzung auf eigene Verantwortung.
