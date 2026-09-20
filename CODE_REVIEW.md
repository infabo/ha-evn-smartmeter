# Codebase-Review: EVN Smart Meter

Datum: 19. September 2026 (konsolidiert aus zwei unabhängigen Reviews vom 18. und 19. September 2026)  
Geprüfter Commit: `be7c92e`  
Umfang: Gesamte Codebasis, nicht nur ein Commit-Diff.

Das Review ergab 23 konkrete Befunde. P1 bezeichnet Fehler, die gespeicherte Energiedaten verfälschen können. P2 bezeichnet Fehler bei Berechnung, Import, Fehlerbehandlung oder Lebenszyklus. P3 bezeichnet Wartbarkeit, Metadaten und Dokumentation. Alle 23 Befunde wurden am 19. September 2026 auf dem Branch `fix/code-review-findings` behoben. Die Statuszeile jedes Befunds nennt den Commit.

Die Befunde 1 bis 8 stammen aus dem ersten Review, die Befunde 9 bis 23 aus dem zweiten. Beide Reviews kamen unabhängig voneinander zu den Befunden 1 bis 8; Befund 1 und 7 wurden im zweiten Durchgang um weitere Szenarien ergänzt.

## 1. [P1] Datenlücken können den kumulativen Energieverbrauch zurücksetzen

**Status:** behoben in Commit `16fbd7a`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:300` — `save_to_home_assistant()`

Die Ermittlung des Ausgangswerts berücksichtigt nur die Stunde unmittelbar vor dem Importfenster. Fehlt diese Stunde, beginnt die Berechnung bei null, obwohl ältere Statistiken vorhanden sind.

**Auswirkung und Reproduktion:** Ein vorhandener kumulativer Verbrauch von 100 kWh wird mit einem neuen Verbrauch von 4 kWh auf 4 kWh statt auf 104 kWh fortgeschrieben.

**Konkretes Szenario im Normalbetrieb:** Das Portal liefert für einen Tag G dauerhaft keine Daten. Der Lauf importiert G-1 und G+1; der letzte Statistikwert liegt danach auf G+1. Der nächste inkrementelle Lauf beginnt bei G+1 (`sensor.py:242-243`), das Importfenster startet um Mitternacht von G+1 und der Ausgangswert wird in der Stunde davor gesucht, also in Stunde 23 des fehlenden Tages G. Die Summe startet bei 0 und das Energy Dashboard zeigt einen negativen Sprung. Ein einzelner fehlender Tag im Portal genügt also für den Reset.

**Zweites Szenario beim Force-Reimport:** Der Reset-Service setzt `last_stats = None` (`sensor.py:196-198`), die Summe beginnt bei 0 und alle neu geholten Stunden werden per Upsert überschrieben. Bricht die Rückwärtssuche früher ab als beim ursprünglichen Import (siehe Befund 5 und 7), bleiben ältere Zeilen mit höherer Summe in der Datenbank stehen. Die Summe fällt an der Nahtstelle ab, was ebenfalls als negativer Verbrauch erscheint.

**Empfehlung:** Den letzten vorhandenen Statistikwert vor dem Importfenster verwenden, auch wenn zwischen diesem Wert und dem Importfenster eine Lücke liegt. `get_last_statistics` liefert diesen Wert bereits; er sollte direkt als Ausgangswert dienen statt einer erneuten Suche in einem Ein-Stunden-Fenster. Beim Force-Reimport vor dem Neuaufbau die vorhandene Statistik löschen (siehe Befund 9).

## 2. [P1] Unterschiedliche Konten überschreiben dieselbe Statistik

**Status:** behoben in Commit `e37e8d4`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:287` — `save_to_home_assistant()`

Alle Konfigurationseinträge verwenden die Statistik-ID `evn_smartmeter:consumption`. Der Konfigurationsdialog erlaubt jedoch mehrere Konten mit unterschiedlichen Benutzernamen.

**Auswirkung und Reproduktion:** Zwei getrennte Konten schreiben unter derselben Statistik-ID. Dadurch überschneiden sich ihre Zeitstempel, und ihre Importe verwenden gemeinsame kumulative Ausgangswerte.

**Empfehlung:** Statistik-IDs und Ziele des Reset-Service eindeutig einem Konto oder Zähler zuordnen. Für bestehende Statistiken ist dabei eine Migration zu berücksichtigen.

## 3. [P1] Zeitumstellungen erzeugen doppelte Zeitstempel

**Status:** behoben in Commit `4f41a1b`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:329` — `save_to_home_assistant()`

Der Import addiert Stunden zu einem lokalen Mitternachtswert ohne Zeitzoneninformation und konvertiert das Ergebnis anschließend nach UTC. Bei Zeitumstellungen entspricht diese lokale Stundenfolge nicht der tatsächlich verstrichenen Zeit.

**Auswirkung und Reproduktion:** Mit der Zeitzone `Europe/Vienna` und dem Verhalten von Home Assistant 2026.1.0 erzeugte ein Frühjahrstag mit 92 Viertelstundenintervallen nur 22 unterschiedliche stündliche Zeitstempel. Bei einem Herbsttag mit 100 Intervallen überschnitt sich der letzte Zeitstempel mit dem Folgetag. Diese Szenarien wurden mit synthetischen Intervallarrays geprüft; das tatsächliche Format der EVN-Antworten an Umstellungstagen wurde nicht live verifiziert.

**Empfehlung:** Die Zeitstempel der Intervalle verwenden oder die verstrichene Zeit ausgehend von einem korrekt bestimmten Tagesbeginn in UTC fortschreiben.

**Referenz:** [Home Assistant 2026.1.0: Zeitzonenkonvertierung in `dt.py`](https://raw.githubusercontent.com/home-assistant/core/2026.1.0/homeassistant/util/dt.py).

## 4. [P2] Der Monatsverbrauch enthält nur den letzten Importumfang

**Status:** behoben in Commit `aca2fc1`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:369` — `_update_monthly()`

Die Berechnung summiert ausschließlich `all_data_by_date`. Bei inkrementellen Importen enthält diese Struktur nur einen Teil des Monats, nicht den gesamten bisherigen Monatsverbrauch.

**Auswirkung und Reproduktion:** Ein Monatswert von 34 kWh fiel nach einem inkrementellen Import auf 4 kWh, obwohl sich der tatsächliche Monatsverbrauch nicht geändert hatte.

**Empfehlung:** Den vollständigen Monatsverbrauch aus persistierten Daten berechnen oder für die Berechnung den gesamten laufenden Monat abrufen.

## 5. [P2] Abruffehler umgehen Wiederholungen und können dauerhafte Lücken hinterlassen

**Status:** behoben in Commit `6526abc`.

**Fundstelle:** `custom_components/evn_smartmeter/smartmeter.py:209` — `get_consumption_per_day()`

Verbindungsfehler werden in leere Ergebnisse umgewandelt. Der Import behandelt diese als fehlende Verbrauchsdaten und meldet einen erfolgreichen Durchlauf. Dadurch wird die geplante Wiederholungslogik nicht ausgelöst. Zusätzlich fängt `_fetch_days()` Ausnahmen ab, ohne den Durchlauf als fehlgeschlagen zu markieren.

**Auswirkung und Reproduktion:** Ein simulierter HTTP-503-Fehler führte zum Status `No data` und zum Rückgabewert `True` aus `async_update()`. Werden nach einem fehlgeschlagenen Tag spätere Tage erfolgreich importiert, liegt der fehlende Tag irgendwann vor dem nächsten inkrementellen Importfenster und wird nicht mehr nachgeladen.

**Empfehlung:** Technische Abruffehler bis zur Wiederholungslogik weiterreichen und unvollständige Tage gezielt nachladen.

## 6. [P2] Nach erneuter Anmeldung wird die fehlgeschlagene Anfrage nicht wiederholt

**Status:** behoben in Commit `b1aa39b`.

**Fundstelle:** `custom_components/evn_smartmeter/smartmeter.py:119` — `_call_api()`

Die Schleife erlaubt nur einen Durchlauf. Nach einer HTTP-401-Antwort erfolgt zwar eine erneute Anmeldung, anschließend wird der Zähler jedoch auf eins erhöht. Damit endet die Schleife unmittelbar in einer Ausnahme.

**Auswirkung und Reproduktion:** Trotz erfolgreicher erneuter Anmeldung wurde die ursprüngliche Anfrage nur einmal ausgeführt. Die vorbereitete erfolgreiche Antwort auf eine zweite Anfrage wurde nie abgerufen.

**Empfehlung:** Nach erfolgreicher erneuter Anmeldung die ursprüngliche Anfrage nochmals ausführen; die Zahl der Wiederholungen weiterhin begrenzen.

## 7. [P2] Fehlende aktuelle Daten verhindern den historischen Import

**Status:** behoben in Commit `2ca5bfa`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:223` — `async_update()`

Ein leeres Ergebnis für den bisher vergangenen Teil des laufenden Monats wird bereits als Ende der gesamten verfügbaren Historie interpretiert.

**Auswirkung und Reproduktion:** Sind die aktuellen September-Daten noch nicht verfügbar, wird der August gar nicht abgefragt, selbst wenn dort Verbrauchsdaten vorhanden sind. Das kann beispielsweise bei einer Installation am 2. September auftreten, bevor die Werte des 1. September veröffentlicht wurden.

**Weiteres Szenario:** Derselbe Abbruch tritt auf, wenn ein vollständiger Kalendermonat mitten in der Historie leer ist, etwa wegen eines Portalausfalls oder eines Zählertauschs. Alle Monate davor werden dann nie importiert, und weil danach nur noch inkrementell geholt wird, bleibt die Historie dauerhaft abgeschnitten.

**Empfehlung:** Einen noch nicht veröffentlichten aktuellen Zeitraum von der tatsächlichen historischen Datengrenze unterscheiden und ältere Monate auch dann prüfen. Die Suche erst nach mehreren aufeinanderfolgenden leeren Monaten oder an einer festen Obergrenze beenden.

## 8. [P2] Änderungen am Importstatus werden nicht an Home Assistant übermittelt

**Status:** behoben in Commit `411f446`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:254` — `async_update()`

Der Import ändert `_state`, veröffentlicht den neuen Zustand aber nicht. Start, Timer und Reset-Service rufen `async_update()` direkt auf. Gleichzeitig ist Polling für diesen Sensor deaktiviert.

**Auswirkung und Reproduktion:** Der interne Zustand änderte sich im isolierten Test, ohne dass ein Zustandsupdate veröffentlicht wurde. In Home Assistant kann die Entität dadurch auf `unknown` oder einem veralteten Status bleiben.

**Empfehlung:** Nach Abschluss eines Durchlaufs den Zustand ausdrücklich veröffentlichen, einschließlich Fehlerzuständen.

**Referenz:** [Home Assistant: Zustandsupdates bei deaktiviertem Polling](https://developers.home-assistant.io/docs/core/entity/#subscribing-to-updates).

## 9. [P2] Der Reset-Service löscht keine Statistiken und räumt nicht auf

**Status:** behoben in Commit `7c59ae0`. Das Löschen der Statistik kam bereits mit `16fbd7a`, das Aufräumen von Service und Sensor-Verweisen mit `e37e8d4`.

**Fundstelle:** `custom_components/evn_smartmeter/__init__.py:22-33` — `handle_reset_statistics()`; `custom_components/evn_smartmeter/__init__.py:45-53` — `async_unload_entry()`

Der Service setzt nur ein Flag, das beim nächsten Lauf `last_stats` ignoriert. Vorhandene Statistikzeilen werden nicht entfernt, sondern per Upsert überschrieben. Zeilen außerhalb des neu ermittelten Zeitraums bleiben bestehen. Der Service wird beim Unload nie deregistriert, und der Sensor-Verweis in `hass.data["evn_smartmeter_sensor"]` wird nicht entfernt.

**Auswirkung:** Nach einem Reimport mit kürzerer Historie bleiben alte Zeilen mit höherer Summe stehen (siehe Befund 1). Nach einem Unload zeigt der Service auf eine entladene Entität, ein Aufruf löst einen Fetch auf einer nicht mehr registrierten Entität aus.

**Empfehlung:** Vor dem Reimport `get_instance(hass).async_clear_statistics([statistic_id])` aufrufen. Service und Sensor-Verweis beim Unload des letzten Eintrags entfernen.

## 10. [P2] Kein Reauth-Flow bei ungültigen Zugangsdaten

**Status:** behoben in Commit `9462b70`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:264-267` — `async_update()`

Bei `SmartmeterLoginError` wird nur der interne Status auf `Login error` gesetzt. Es wird weder eine Reparatur ausgelöst noch der Nutzer benachrichtigt. Der Config Flow bietet keinen Reauth-Schritt.

**Auswirkung:** Nach einer Passwortänderung im Portal schlagen alle Läufe still fehl. Der Nutzer muss den Eintrag löschen und neu anlegen, was die Statistik-Zuordnung riskiert.

**Empfehlung:** `entry.async_start_reauth(hass)` auslösen und `async_step_reauth` sowie `async_step_reauth_confirm` im Config Flow ergänzen.

## 11. [P2] Parallele Läufe teilen sich den API-Client

**Status:** behoben in Commit `320ebe3`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:184` und `sensor.py:277` — `async_update()`

Ein Service-Aufruf, der Startfetch und der geplante Fetch können gleichzeitig laufen. Jeder Lauf setzt `self._api` neu; das `finally` des zuerst endenden Laufs schließt den Client, den der andere Lauf gerade benutzt.

**Auswirkung:** Der zweite Lauf bricht mit Verbindungsfehlern ab oder importiert unvollständig. Beide Läufe schreiben außerdem mit unterschiedlichen Ausgangssummen in dieselbe Statistik.

**Empfehlung:** `async_update()` mit einem `asyncio.Lock` serialisieren und den Client lokal statt als Instanzattribut halten.

## 12. [P2] Entitäten ohne `unique_id`

**Status:** behoben in Commit `2438333`. Die `unique_id` kam bereits mit `e37e8d4`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:138-145` — `EVNSmartmeterSensor.__init__()`; `sensor.py:383-390` — `EVNSmartmeterMonthlySensor.__init__()`

Keine der beiden Entitäten setzt `_attr_unique_id`. Der Monatssensor erzwingt stattdessen eine hartkodierte `entity_id`.

**Auswirkung:** Es entstehen keine Einträge in der Entity Registry. Der Nutzer kann die Entitäten nicht umbenennen, deaktivieren oder einem Bereich zuordnen. Bei mehreren Konten kollidiert die hartkodierte `entity_id`.

**Empfehlung:** `_attr_unique_id` aus `entry.entry_id` ableiten, `entity_id` nicht mehr hartkodieren, optional ein `DeviceInfo` pro Zählpunkt ergänzen.

## 13. [P2] Datumsermittlung nutzt die Systemzeitzone

**Status:** behoben in Commit `4f41a1b` zusammen mit Befund 3.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:204` und `sensor.py:368`

`date.today()` liefert das Datum der Systemzeitzone des Hosts, nicht der in Home Assistant konfigurierten Zeitzone.

**Auswirkung:** Läuft Home Assistant auf einem Host mit UTC, weicht das Datum zwischen 22:00 und 24:00 lokaler Zeit ab. Der Lauf holt dann einen Tag zu wenig, und der Monatswechsel des Monatssensors verschiebt sich.

**Empfehlung:** `dt_util.now().date()` verwenden.

## 14. [P2] Der Startfetch hat weder Retry noch Abbruch beim Unload

**Status:** behoben in Commit `b58165c`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:57` — `async_setup_entry()`

Der erste Fetch nach Start oder Reload läuft über `hass.async_create_task`, der Rückgabewert wird nicht ausgewertet, und der Task ist nicht an den Config Entry gebunden.

**Auswirkung:** Ein Verbindungsfehler beim Start wird erst beim nächsten geplanten Lauf nachgeholt. Bei einem Reload während des Erstimports läuft der alte Task weiter und schreibt parallel zum neuen (siehe Befund 11).

**Empfehlung:** `entry.async_create_task` verwenden und das Ergebnis wie in `_run()` behandeln, sodass bei Verbindungsfehlern die Retry-Kette greift.

## 15. [P2] `No data` löst keine Wiederholung aus

**Status:** behoben in Commit `bb4c4e5`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:258-262` — `async_update()`

Ein leeres Ergebnis wird als erfolgreicher Lauf gewertet und liefert `True`. Das Portal stellt die Vortagswerte jedoch häufig erst im Laufe des Vormittags bereit.

**Auswirkung:** Fällt der zufällige Abrufzeitpunkt vor die Bereitstellung, fehlt der Tag bis zum nächsten Morgen. Zusammen mit Befund 5 lässt sich ein echter Fehler nicht von noch nicht veröffentlichten Daten unterscheiden.

**Empfehlung:** `No data` innerhalb des konfigurierten Zeitfensters wie einen transienten Fehler behandeln und die Retry-Logik nutzen.

## 16. [P3] Timer-Abmeldungen sammeln sich bis zum Reload an

**Status:** behoben in Commit `b58165c` zusammen mit Befund 14.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:95-96` — `_schedule_next_fetch()`; `sensor.py:129-130` — `_schedule_retry()`

Jede Planung registriert ihre Abmeldefunktion per `entry.async_on_unload(unsub)`. Nach dem Auslösen bleibt der Eintrag in der Liste des Config Entry erhalten.

**Auswirkung:** Pro Tag ein weiterer Eintrag in der Unload-Liste, bis zum nächsten Reload. Funktional harmlos, aber ein wachsender Bestand an toten Callbacks.

**Empfehlung:** Die aktuelle Abmeldefunktion in einem Attribut halten und vor der Neuplanung aufrufen, oder `async_track_time_change` für einen festen Tageszeitpunkt nutzen.

## 17. [P3] Eigener httpx-Client statt des Home-Assistant-Helfers

**Status:** behoben in Commit `f03df3c`. Statt `create_async_httpx_client` wird HAs vorgefertigter SSL-Kontext genutzt, weil der Helfer `aclose()` mit einer Warnung umhüllt und pro Client einen Shutdown-Listener registriert.

**Fundstelle:** `custom_components/evn_smartmeter/smartmeter.py:60` — `authenticate()`

Der Client wird per `asyncio.to_thread(httpx.AsyncClient)` erzeugt, um das Laden der SSL-Zertifikate aus dem Event Loop zu halten.

**Auswirkung:** Kein Fehler, aber ein Workaround für ein Problem, das Home Assistant bereits löst. Die Abhängigkeit `httpx>=0.27.0` im Manifest ist zusätzlich unnötig, da Home Assistant httpx mitliefert.

**Empfehlung:** `create_async_httpx_client(hass, timeout=30)` aus `homeassistant.helpers.httpx_client` verwenden und den Client in den Konstruktor von `Smartmeter` reichen.

## 18. [P3] Mindestversion in `hacs.json` stimmt nicht

**Status:** behoben in Commit `bbda49f`. Gesetzt auf die in der README dokumentierte Version 2026.1.0; die verifizierte technische Untergrenze liegt bei 2025.11.0.

**Fundstelle:** `hacs.json:4`; `custom_components/evn_smartmeter/sensor.py:340-345`

`hacs.json` nennt Home Assistant 2024.1.0 als Minimum. `StatisticMeanType` und `unit_class` in `StatisticMetaData` sowie der `OptionsFlow` ohne `config_entry`-Konstruktor setzen etwa 2025.10 voraus. Die README nennt 2026.1.

**Auswirkung:** HACS erlaubt die Installation auf Versionen, auf denen der Import beim ersten Lauf mit einem Importfehler abbricht.

**Empfehlung:** `homeassistant` in `hacs.json` auf den in der README genannten Stand setzen.

## 19. [P3] README beschreibt nicht mehr das aktuelle Verhalten

**Status:** behoben in Commit `402e8ae`.

**Fundstelle:** `README.md`, Abschnitte „Features“, „Architektur“ und „Hinweise“

Die README nennt einen festen Abruf um 06:00 über `async_track_time_change`, einen 7-Tage-Lookback und einen Erstimport der letzten 7 Tage. Der Code holt zu einem zufälligen Zeitpunkt im konfigurierbaren Fenster, importiert beim ersten Lauf die gesamte Historie und danach inkrementell. Zeitfenster-Optionen und der Service `reset_statistics` sind nicht dokumentiert.

**Empfehlung:** Feature-Liste, Architekturabschnitt und Hinweise an den aktuellen Stand anpassen, Optionen und Service ergänzen.

## 20. [P3] `services.yaml` im alten Format

**Status:** behoben in Commit `30b75bb`.

**Fundstelle:** `custom_components/evn_smartmeter/services.yaml`

Name und Beschreibung stehen direkt in der YAML-Datei. Aktuelle Integrationen definieren diese Texte unter `services` in `strings.json` und den Übersetzungen.

**Auswirkung:** Der Service erscheint nur auf Englisch, die deutsche Übersetzung greift nicht.

**Empfehlung:** `services.yaml` auf die Felddefinition reduzieren und Name sowie Beschreibung in `strings.json`, `translations/de.json` und `translations/en.json` übertragen.

## 21. [P3] Erfolgsmeldungen auf Warning-Level

**Status:** behoben in Commit `bb4c4e5` zusammen mit Befund 15. Die verbleibenden Warnungen betreffen echte Fehler oder löschende Operationen.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:198` und `sensor.py:255`

„Force reimport“ und „EVN import complete“ werden mit `_LOGGER.warning` protokolliert.

**Auswirkung:** Jeder erfolgreiche Tageslauf erzeugt eine Warnung im Home-Assistant-Log und überdeckt echte Warnungen.

**Empfehlung:** `_LOGGER.info` verwenden.

## 22. [P3] Sensor-API wird umgangen

**Status:** behoben in Commit `f81227f`.

**Fundstelle:** `custom_components/evn_smartmeter/sensor.py:147-149` und `sensor.py:392-394` — `state`; `sensor.py:387` — `_attr_state_class`

Beide Entitäten überschreiben `state` statt `native_value`. Der Monatssensor setzt `_attr_state_class` als String `"total_increasing"` statt `SensorStateClass.TOTAL_INCREASING`.

**Auswirkung:** Die Einheiten- und Wertvalidierung von `SensorEntity` sowie die Einheitenumrechnung durch den Nutzer werden umgangen. Funktioniert derzeit, bricht bei Änderungen an `SensorEntity` ohne Vorwarnung.

**Empfehlung:** `_attr_native_value` beziehungsweise `native_value` verwenden, `SensorStateClass` und `UnitOfEnergy` nutzen.

## 23. [P3] Toter Code

**Status:** behoben in Commit `2b43868`.

**Fundstelle:** `custom_components/evn_smartmeter/smartmeter.py:132-137` — `get_user_details()`; `smartmeter.py:213-239` — `get_consumption_for_month()`; `smartmeter.py:52` — `except (httpx.RequestError, TypeError)`; `custom_components/evn_smartmeter/__init__.py:19` — `hass.data[DOMAIN][entry.entry_id]`

Die beiden API-Methoden werden nirgends aufgerufen. Der `TypeError` im Except-Zweig hat keinen erkennbaren Auslöser. `hass.data[DOMAIN][entry.entry_id]` wird gesetzt, aber nie gelesen.

**Empfehlung:** Entfernen oder mit einem Verwendungszweck versehen.

## Validierung und Grenzen

- Acht isolierte Reproduktionstests bestätigten die beschriebenen Fehlerszenarien. Die Tests prüfen das beobachtete fehlerhafte Verhalten; ihr Bestehen bedeutet nicht, dass die Integration korrekt arbeitet.
- Die Reproduktionen führten unveränderte Funktionskörper aus dem Repository mit vereinfachten Home-Assistant-Schnittstellen aus. Die Zeitzonenkonvertierung orientierte sich an Home Assistant 2026.1.0.
- Alle sechs Python-Dateien und alle fünf JSON-Dateien ließen sich erfolgreich parsen.
- Die im ersten Review genannten acht Reproduktionstests liegen nicht im Repository.
- Die Befunde 9 bis 23 sowie die Ergänzungen zu Befund 1 und 7 beruhen auf Code-Analyse ohne Reproduktionstests.
- Im Repository war keine Testsuite vorhanden. Home Assistant war in der Prüfungsumgebung nicht installiert.
- Es fand kein Test in einer laufenden Home-Assistant-Instanz und kein Zugriff auf ein echtes EVN-Konto statt.
- Der Anwendungscode wurde nicht verändert. Diese Markdown-Datei dokumentiert ausschließlich das Review.
