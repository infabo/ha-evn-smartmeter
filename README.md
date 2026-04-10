# EVN Smart Meter – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/HA-2024.1%2B-blue.svg)](https://www.home-assistant.io/)

Inoffizielle Home Assistant Integration für das [EVN / Netz Niederösterreich Smart Meter Portal](https://smartmeter.netz-noe.at/).

Zeigt Stromverbrauchsdaten deines Smart Meters als Sensoren in Home Assistant an – kompatibel mit dem **Energy Dashboard**.

## Features

- 🔌 **Gesamtverbrauch** – kumulativer Zählerstand (kWh), kompatibel mit dem Energy Dashboard
- 📊 **Tagesverbrauch** – aktueller Verbrauch des heutigen Tages (kWh)
- 🔄 Automatisches Update alle 30 Minuten (Smart Meter sendet 15-Min-Intervalle)
- 🔐 Login über Username/Password des Smart Meter Portals
- 💾 Zustand wird über HA-Neustarts hinweg beibehalten

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
4. Fertig! Die Sensoren werden automatisch erstellt.

## Energy Dashboard

1. **Einstellungen** → **Dashboards** → **Energie**
2. Unter „Stromnetz" → **Verbrauch hinzufügen**
3. Sensor „EVN Smart Meter Total consumption" auswählen
4. Speichern

## Sensoren

| Sensor | Beschreibung | State Class | Einheit |
|--------|-------------|-------------|---------|
| Total consumption | Kumulativer Gesamtverbrauch | `total_increasing` | kWh |
| Daily consumption | Heutiger Tagesverbrauch | `total` | kWh |

## Hinweise

- Die Daten stammen von der privaten API des Smart Meter Portals und können mit einer Verzögerung von einigen Minuten bis Stunden eintreffen.
- Der Gesamtverbrauch startet bei 0 ab dem Zeitpunkt der Installation. Historische Daten werden nicht rückwirkend importiert.
- Die Integration nutzt die [PyNoeSmartmeter](https://github.com/Xlinx64/PyNoeSmartmeter) Library.

## Credits

- [PyNoeSmartmeter](https://github.com/Xlinx64/PyNoeSmartmeter) von David Illichmann – Python-Wrapper für die Netz NÖ API
- [EVN Smartmeter Wrapper](https://www.lteforum.at/mobilfunk/evn-smartmeter-api-wrapper-influx-importer-grafana-dashboard.21319/) von A.E.I.O.U.

## Lizenz

MIT License

## Disclaimer

Dies ist kein offizielles Produkt von EVN oder Netz Niederösterreich GmbH. Nutzung auf eigene Verantwortung.
