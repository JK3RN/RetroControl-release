# Prüfung des Release-Kandidaten 3.1.0

Geprüft am 22.11.2026 auf Arch Linux, Python 3.14.8, GTK 4.24.1, PyGObject 3.58.1, Cairo 1.18.6 und Pycairo 1.29.2. SHA256SUMS bindet den Bericht an die ausgelieferten Dateien.

| Prüfung | Ergebnis |
| --- | --- |
| Unit- und Prozessregressionen | PASS, 32 von 32 auf dem Host. Abgedeckt sind u. a. Start/Stopp, Neustart, Prozessidentität, Registry-Parameter, Telemetrie und Dateisicherheit. |
| Privates Zustandsverzeichnis | PASS, neuer Ordner mit 0700. Gemeinsam beschreibbarer Ordner, Verzeichnis-Symlink sowie symbolisch oder hart verlinkte Steuerdatei abgewiesen. |
| GTK ohne installierte Beispiel-App | PASS, Fenster geöffnet, Editor ausgewählt, Startversuch zeigt den fehlenden Ordner im Detailbereich. |
| Installation und Deinstallation | PASS, relativen Link in temporärem bin-Verzeichnis zweimal installiert, Status über Link gelesen, Link entfernt. |
| Showcase | PASS, echtes GTK-Fenster mit separater Beispiel-Registry, ohne sichtbare lokale Pfade oder Benutzernamen. |
| Andere Distributionen und älteste Python-Version | SKIP, kein entsprechender Laufzeittest. |

Im Dateisystem-Sandboxlauf wurden 19 Tests mit Fehler abgebrochen, weil Systempfade auf eine fremde UID abgebildet waren. Der Hostlauf verwendete die tatsächlichen Eigentümer. Für Container mit abweichenden Eigentümern kann die Zustandsprüfung den Start verweigern.

Die Arch-Sicherheitsdatenbank lieferte beim Abgleich der fünf genannten Paketnamen keine offenen Einträge. Dieser Abgleich umfasst die direkten Systempakete. Die Anwendungen der Beispielansicht wurden für den Release weder eingebunden noch gestartet.

Die Registry ist vertrauenswürdige lokale Konfiguration. Die Prozesssuche beschränkt sich auf Prozesse des eigenen Benutzers und berücksichtigt Argumente und Installationsort. GTK meldet im Test eine DeprecationWarning für die CSS-Ladefunktion. Der Desktop-Smoke bestand.
