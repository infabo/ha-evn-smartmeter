# Changelog

Alle nennenswerten Änderungen an dieser Integration werden hier dokumentiert.

Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionierung an [Semantic Versioning](https://semver.org/lang/de/).

## [1.1.0] – 2026-09-20

Sammelrelease aus einem vollständigen Code-Review der Codebasis. 23 Befunde wurden
behoben, darunter mehrere, die gespeicherte Energiedaten verfälschen konnten.

### ⚠️ Vor dem Update lesen

- **Home Assistant 2026.1.0 oder neuer wird jetzt vorausgesetzt.** Bisher stand in
  `hacs.json` 2024.1.0, obwohl der Code längst neuere Schnittstellen nutzt. Auf älteren
  Versionen brach der Import mit einem Importfehler ab.
- Die Konfiguration wird beim ersten Start automatisch auf Version 2 migriert. Das
  zuerst eingerichtete Konto behält die Statistik-ID `evn_smartmeter:consumption`,
  bestehende Energy-Dashboard-Konfigurationen bleiben also gültig.
- Zeigte dein Energy Dashboard bisher negative oder springende Werte, lag das an den
  unten beschriebenen Summenfehlern. Ein Aufruf des Dienstes
  `evn_smartmeter.reset_statistics` baut die Historie sauber neu auf.

### Behoben

- **Kumulative Summe wurde durch Datenlücken zurückgesetzt.** Fehlte ein Tag im Portal,
  begann die Summe beim nächsten Lauf wieder bei null und das Energy Dashboard zeigte
  einen negativen Sprung. Die Summe wird jetzt aus dem letzten gespeicherten Wert vor
  dem Importfenster fortgeschrieben, unabhängig davon, wie groß die Lücke davor ist.
- **Mehrere Konten überschrieben sich gegenseitig.** Alle Einträge schrieben in
  `evn_smartmeter:consumption`. Jedes Konto bekommt nun eine eigene Statistik.
- **Zeitumstellung erzeugte falsche Zeitstempel.** Ein Frühjahrstag verlor eine Stunde
  an einen doppelten Zeitstempel, ein Herbsttag überschrieb Mitternacht des Folgetags.
  Stundenwerte werden jetzt als verstrichene Zeit ab lokaler Mitternacht berechnet.
- **Monatsverbrauch zeigte nur den letzten Import.** Der Wert fiel nach jedem
  inkrementellen Lauf auf den Verbrauch weniger Tage und stand nach einem Neustart auf
  null. Er wird jetzt aus den gespeicherten Statistiken berechnet.
- **Verbindungsfehler galten als „keine Daten".** Ein Portalausfall konnte den
  Erstimport vorzeitig beenden und die Historie dauerhaft abschneiden. Technische
  Fehler werden jetzt als Fehler behandelt und wiederholt.
- **Erstimport brach beim ersten leeren Monat ab.** War der laufende Monat noch nicht
  veröffentlicht oder fehlte ein Monat mitten in der Historie, wurden alle älteren
  Monate nie geholt. Die Suche läuft jetzt weiter, bis drei Monate in Folge leer sind.
- **Nach Ablauf der Sitzung ging ein Tag verloren.** Die Anfrage wurde nach der
  erneuten Anmeldung nicht wiederholt.
- **Import-Status blieb im UI auf `unknown`.** Der Sensor veröffentlichte seinen
  Zustand nie.
- **Reset-Dienst löschte keine Statistiken**, sondern überschrieb sie nur teilweise.
  Er löscht jetzt vorher und läuft über alle eingerichteten Konten.
- **Gleichzeitige Läufe schlossen sich gegenseitig die Verbindung.** Timer, Start,
  Wiederholung und Dienst laufen jetzt nacheinander.
- **Datumsberechnung nutzte die Systemzeitzone** statt der in Home Assistant
  eingestellten.
- **Kein erneuter Versuch bei fehlgeschlagenem Start** und keiner, wenn das Portal die
  Vortagswerte noch nicht veröffentlicht hatte. Beides wird jetzt bis zu zweimal im
  Abstand von 30 Minuten wiederholt.
- **Timer-Callbacks sammelten sich** mit jeder Neuplanung an.
- **Laufende Importe überlebten einen Reload.** Der beim Start ausgelöste Import war
  nicht an die Integration gebunden und lief nach einem Neuladen weiter, parallel zum
  neuen. Importe laufen jetzt als Background-Task und werden beim Entladen abgebrochen.

### Neu

- **Erneute Anmeldung.** Lehnt das Portal die Zugangsdaten ab, fragt Home Assistant
  nach dem neuen Passwort, statt täglich still zu scheitern.
- **Gerät pro Konto.** Beide Sensoren hängen an einem Gerät und lassen sich einem
  Bereich zuordnen, umbenennen und deaktivieren.
- **Übersetzter Dienst.** Name und Beschreibung von `reset_statistics` erscheinen jetzt
  auf Deutsch.

### Geändert

- Der HTTP-Client nutzt den vorgefertigten SSL-Kontext von Home Assistant. Die
  Abhängigkeit `httpx` entfällt, da Home Assistant sie mitbringt.
- Die README beschreibt wieder das tatsächliche Verhalten. Das einstellbare
  Abrufzeitfenster, der Reset-Dienst und die Wiederholungslogik waren nicht
  dokumentiert.
- Der Monatssensor meldet über die reguläre Sensor-Schnittstelle statt über einen
  überschriebenen Zustand.
- Der Dienst `reset_statistics` kehrt jetzt sofort zurück und arbeitet im Hintergrund.
  Bisher blockierte der Aufruf, bis die gesamte Historie neu geladen war, was bei
  langen Importen den aufrufenden Automatisierungsschritt aufhielt.
- Zwei ungenutzte API-Methoden entfernt.

## [1.0.4] – davor

Siehe Git-Historie.
