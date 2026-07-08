# Verfügbarkeitsplaner

Flask-App mit Login, mehreren Boards, Änderungsprotokoll und PDF-Export.

## Funktionen

- Registrierung / Login (eigenes System, kein externer Anbieter)
- **Registrierung nur mit gültigem Einladungslink** (Ausnahme: der allererste Account, der automatisch Administrator wird)
- Drei Rollen:
  - **Administrator**: alles — Nutzer/Rollen verwalten, Boards & Einladungen anlegen/löschen, jede Zeile bearbeiten, PDF-Export
  - **Lehrer**: darf Personen/Termine in Boards verwalten, jede Zeile bearbeiten, PDF-Export — aber keine Nutzer-/Board-Verwaltung
  - **Nutzer**: sieht alle Boards, trägt sich per „Mich hinzufügen“ selbst ein und darf **nur die eigene Zeile** bearbeiten
- Beliebig viele Boards, pro Board eigene Personen, Termine und Status
- Änderungsprotokoll pro Board (nur für Admin/Lehrer sichtbar)
- PDF-Export pro Board (nur Admin/Lehrer, ReportLab, keine Systemabhängigkeiten)
- Sicherheit: gehashte Passwörter, CSRF-Schutz auf allen Formularen, Rate-Limiting gegen Passwort-Raten beim Login

## Einladungslinks

Im Adminbereich unter „Einladungen“ lässt sich ein Link erzeugen mit:
- optionaler Bezeichnung (z.B. „Klasse 10b“)
- maximaler Nutzungsanzahl (einmalig, mehrfach, unbegrenzt)
- optionalem Ablaufdatum

Der erzeugte Link (`.../register?invite=CODE`) kann kopiert und z.B. per Discord/WhatsApp verschickt werden.
Ohne gültigen Link ist `/register` gesperrt.

## Lokal starten

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Die App läuft dann auf `http://localhost:5000`. Ohne `DATABASE_URL` wird automatisch eine lokale
SQLite-Datei (`local.db`) verwendet — praktisch zum Testen, aber **nicht** für den Produktivbetrieb
auf Render (siehe unten).

## Deployment auf Render

### Variante A: Mit `render.yaml` (empfohlen, ein Klick)

1. Projekt in ein GitHub-Repository pushen.
2. Auf [render.com](https://render.com) → **New** → **Blueprint** → das Repository auswählen.
3. Render liest `render.yaml` automatisch aus und legt an:
   - einen Web-Service (Python, `gunicorn app:app`)
   - eine PostgreSQL-Datenbank (kostenloser Plan)
   - verbindet beides automatisch über `DATABASE_URL`
   - generiert automatisch einen `SECRET_KEY`
4. Deploy abwarten, fertig.

### Variante B: Manuell

1. Auf Render: **New** → **PostgreSQL** anlegen, „Internal Database URL“ kopieren.
2. **New** → **Web Service** → Repository verbinden.
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
3. Environment-Variablen setzen:
   - `DATABASE_URL` → die kopierte Postgres-URL
   - `SECRET_KEY` → ein beliebiger langer Zufallsstring
   - `FLASK_ENV` → `production`
4. Deploy starten.

### Wichtig

- **Kostenloser Render-Webservice-Plan:** die Festplatte des Webservice ist nicht dauerhaft — deshalb
  läuft die App gegen eine **separate PostgreSQL-Datenbank**, nicht gegen eine lokale Datei. Die
  Datenbank selbst bleibt bei Redeploys erhalten.
- **Erster Nutzer = Administrator.** Registriere dich als Erstes selbst, danach kannst du im
  Adminbereich (`/admin`) weitere Personen zu Admins machen.
- Die kostenlose Render-Postgres-Datenbank wird nach Ablauf der Testphase kostenpflichtig bzw.
  läuft aus — für Dauerbetrieb ggf. einen bezahlten Datenbankplan wählen.

## Projektstruktur

```
app.py              Routen, Auth, API, PDF-Export, Admin
models.py            SQLAlchemy-Modelle
templates/           Jinja-Templates (Login, Dashboard, Board, Admin)
static/style.css     Design (Light/Dark automatisch)
static/app.js        Grid-Interaktivität (fetch-Aufrufe an die API)
render.yaml           Render-Blueprint für automatisches Deployment
```

## Mögliche nächste Schritte

- E-Mail-Login-Code / Passwort-Reset per Mail (braucht einen externen E-Mail-Dienst wie Resend)
- Boards auf bestimmte Nutzer/Rollen beschränken (aktuell sehen alle eingeloggten Nutzer alle Boards)
- Excel-Export ergänzen (openpyxl)
