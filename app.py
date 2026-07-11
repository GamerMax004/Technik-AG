import os
import secrets
from datetime import datetime, timedelta, date as date_cls
from io import BytesIO

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort, send_file
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import func

from models import db, User, Board, Person, EventDate, Entry, ChangeLog, Invite, BoardManager, AccessRequest, STATUS_VALUES, ROLE_LABELS
from moderation import contains_banned_word

# ---------------------------------------------------------------------------
# App / DB setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

database_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
# Render liefert postgres:// - SQLAlchemy 2.x will postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB, v.a. wegen PDF-Anhängen

app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"

db.init_app(app)
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Bitte melde dich an, um fortzufahren."


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(fn):
    """Nur echte Administratoren (Nutzer-/Boardverwaltung)."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


def staff_required(fn):
    """Admin oder Lehrer: Board-Inhalte verwalten, PDF exportieren."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_staff:
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


def can_edit_person(person):
    """Staff darf jede Zeile bearbeiten, normale Nutzer nur ihre eigene."""
    return current_user.is_staff or person.user_id == current_user.id


def can_access_board(board_id, user):
    """Admin sieht immer alles. Lehrer nur zugewiesene Boards. Normale Nutzer
    nur Boards, in denen sie als Person eingetragen sind (wird separat geprüft)."""
    if user.is_admin:
        return True
    if user.role == "lehrer":
        return BoardManager.query.filter_by(board_id=board_id, user_id=user.id).first() is not None
    return None  # normale Nutzer: Prüfung erfolgt über die Person-Zuordnung


def lehrer_board_ids(user_id):
    return [m.board_id for m in BoardManager.query.filter_by(user_id=user_id).all()]


def log_action(board_id, action, detail=None):
    entry = ChangeLog(
        board_id=board_id,
        user_id=current_user.id if current_user.is_authenticated else None,
        action=action,
        detail=detail,
    )
    db.session.add(entry)


STATUS_LABEL = {"unset": "Offen", "ok": "Verfügbar", "warn": "Mit Vorbehalt", "no": "Nicht verfügbar"}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    invite_code = request.values.get("invite", "").strip()
    invite = Invite.query.filter_by(code=invite_code).first() if invite_code else None
    invite_ok = invite is not None and invite.is_valid()

    # Ausnahme: Solange noch kein einziger Nutzer existiert, darf sich der
    # allererste Account (Administrator) auch ohne Einladung registrieren.
    is_bootstrap = User.query.count() == 0
    if not invite_ok and not is_bootstrap:
        return render_template("register.html", invite_missing=True)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip() or username
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        error = None
        if len(username) < 3:
            error = "Benutzername muss mindestens 3 Zeichen haben."
        elif len(password) < 6:
            error = "Passwort muss mindestens 6 Zeichen haben."
        elif password != password2:
            error = "Passwörter stimmen nicht überein."
        elif User.query.filter(func.lower(User.username) == username.lower()).first():
            error = "Dieser Benutzername ist bereits vergeben."
        elif contains_banned_word(username, include_reserved=True) or contains_banned_word(display_name):
            error = "Dieser Name ist nicht erlaubt. Bitte wähle einen anderen Benutzer- oder Anzeigenamen."

        if error:
            flash(error, "error")
            return render_template("register.html", username=username, display_name=display_name, invite=invite_code)

        is_first_user = User.query.count() == 0
        user = User(
            username=username,
            display_name=display_name,
            role="admin" if is_first_user else "user",
        )
        user.set_password(password)
        db.session.add(user)

        if invite_ok:
            invite.uses_count += 1
            if invite.max_uses and invite.uses_count >= invite.max_uses:
                invite.active = False

        db.session.commit()

        login_user(user)
        flash("Konto erstellt." + (" Du bist der erste Nutzer und damit Administrator." if is_first_user else ""), "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html", invite=invite_code)


@app.route("/register/request-access", methods=["POST"])
@limiter.limit("5 per hour")
def request_access():
    email = request.form.get("email", "").strip()
    invite_code = request.values.get("invite", "").strip()

    if not email or "@" not in email or "." not in email.split("@")[-1] or len(email) > 255:
        flash("Bitte eine gültige E-Mail-Adresse angeben.", "error")
        return redirect(url_for("register", invite=invite_code))

    db.session.add(AccessRequest(email=email[:255]))
    db.session.commit()
    flash("Anfrage gesendet. Du wirst kontaktiert, sobald ein Zugang für dich freigeschaltet wurde.", "success")
    return redirect(url_for("register", invite=invite_code))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(func.lower(User.username) == username.lower()).first()

        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))

        flash("Benutzername oder Passwort ist falsch.", "error")
        return render_template("login.html", username=username)

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard / Boards
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    if current_user.is_admin:
        boards = Board.query.order_by(Board.created_at.desc()).all()
    elif current_user.role == "lehrer":
        ids = lehrer_board_ids(current_user.id)
        boards = Board.query.filter(Board.id.in_(ids)).order_by(Board.created_at.desc()).all() if ids else []
    else:
        my_board_ids = [p.board_id for p in Person.query.filter_by(user_id=current_user.id).all()]
        boards = (
            Board.query.filter(Board.id.in_(my_board_ids)).order_by(Board.created_at.desc()).all()
            if my_board_ids else []
        )
    return render_template("dashboard.html", boards=boards)


@app.route("/board/<int:board_id>")
@login_required
def board_view(board_id):
    board = db.session.get(Board, board_id) or abort(404)
    all_people = board.people
    # Lehrer und Admin sind Verwalter, keine Teilnehmer: sie erscheinen nicht in der Personenliste.
    people = [p for p in all_people if not p.user or p.user.role != "lehrer"]
    dates = board.dates

    my_person = next((p for p in people if p.user_id == current_user.id), None)

    # Zugriffskontrolle: Admin immer, Lehrer nur zugewiesene Boards, normale Nutzer nur eigene Person.
    if current_user.role == "lehrer":
        if not can_access_board(board_id, current_user):
            abort(403)
    elif not current_user.is_staff and my_person is None:
        abort(403)

    entries = {}
    for e in Entry.query.filter_by(board_id=board_id).all():
        entries[f"{e.person_id}:{e.date_id}"] = e.status

    weekdays = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    date_info = []
    for d in dates:
        stats = {"ok": 0, "warn": 0, "no": 0, "unset": 0}
        for p in people:
            st = entries.get(f"{p.id}:{d.id}", "unset")
            stats[st] += 1
        date_info.append({
            "id": d.id,
            "date": d.date.isoformat(),
            "weekday": weekdays[d.date.weekday()],
            "display": d.date.strftime("%d.%m."),
            "label": d.label or "",
            "stats": stats,
            "my_status": entries.get(f"{my_person.id}:{d.id}", "unset") if my_person else "unset",
            "attachment": d.attachment_filename,
        })

    # Änderungsprotokoll: ausschließlich für echte Administratoren, nicht für Lehrer.
    recent_logs = ChangeLog.query.filter_by(board_id=board_id).order_by(ChangeLog.timestamp.desc()).limit(50).all() if current_user.is_admin else []

    already_ids = {p.user_id for p in all_people if p.user_id}
    available_users = (
        User.query.filter(User.role != "lehrer", ~User.id.in_(already_ids)).order_by(User.display_name).all()
        if already_ids else
        User.query.filter(User.role != "lehrer").order_by(User.display_name).all()
    )

    # Nur für Administratoren: Lehrkräfte für dieses Board freischalten/entziehen.
    board_lehrer = []
    available_lehrer = []
    if current_user.is_admin:
        board_lehrer = BoardManager.query.filter_by(board_id=board_id).order_by(BoardManager.added_at).all()
        assigned_ids = {m.user_id for m in board_lehrer}
        available_lehrer = User.query.filter(User.role == "lehrer", ~User.id.in_(assigned_ids)).order_by(User.display_name).all() if assigned_ids \
            else User.query.filter(User.role == "lehrer").order_by(User.display_name).all()

    return render_template(
        "board.html",
        board=board,
        people=people,
        date_info=date_info,
        entries=entries,
        recent_logs=recent_logs,
        available_users=available_users,
        my_person=my_person,
        board_lehrer=board_lehrer,
        available_lehrer=available_lehrer,
    )


# ---------------------------------------------------------------------------
# Board API (people / dates / entries)
# ---------------------------------------------------------------------------

@app.route("/api/board/<int:board_id>/person", methods=["POST"])
@login_required
@staff_required
def api_add_person(board_id):
    db.session.get(Board, board_id) or abort(404)
    data = request.json or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "Nutzer fehlt"}), 400

    user = db.session.get(User, user_id) or abort(404)
    if user.role == "lehrer":
        return jsonify({"error": "Lehrkräfte können nicht als Person hinzugefügt werden"}), 400
    if Person.query.filter_by(board_id=board_id, user_id=user.id).first():
        return jsonify({"error": "Dieser Nutzer ist bereits in diesem Board"}), 400

    max_order = db.session.query(func.max(Person.sort_order)).filter_by(board_id=board_id).scalar() or 0
    person = Person(board_id=board_id, name=user.display_name[:60], sort_order=max_order + 1, user_id=user.id)
    db.session.add(person)
    log_action(board_id, "Nutzer hinzugefügt", user.display_name)
    db.session.commit()
    return jsonify({"id": person.id, "name": person.name})


@app.route("/api/person/<int:person_id>", methods=["PATCH"])
@login_required
def api_rename_person(person_id):
    person = db.session.get(Person, person_id) or abort(404)
    if not can_edit_person(person):
        abort(403)
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "Name fehlt"}), 400
    old_name = person.name
    person.name = name[:60]
    log_action(person.board_id, "Person umbenannt", f"{old_name} -> {person.name}")
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/person/<int:person_id>", methods=["DELETE"])
@login_required
def api_delete_person(person_id):
    person = db.session.get(Person, person_id) or abort(404)
    if not can_edit_person(person):
        abort(403)
    board_id = person.board_id
    log_action(board_id, "Person entfernt", person.name)
    db.session.delete(person)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/board/<int:board_id>/lehrer", methods=["POST"])
@login_required
@admin_required
def api_add_board_lehrer(board_id):
    db.session.get(Board, board_id) or abort(404)
    user_id = (request.json or {}).get("user_id")
    if not user_id:
        return jsonify({"error": "Lehrkraft fehlt"}), 400
    user = db.session.get(User, user_id) or abort(404)
    if user.role != "lehrer":
        return jsonify({"error": "Nur Lehrkräfte können hier freigeschaltet werden"}), 400
    if BoardManager.query.filter_by(board_id=board_id, user_id=user.id).first():
        return jsonify({"error": "Diese Lehrkraft hat bereits Zugriff"}), 400

    manager = BoardManager(board_id=board_id, user_id=user.id)
    db.session.add(manager)
    log_action(board_id, "Lehrkraft freigeschaltet", user.display_name)
    db.session.commit()
    return jsonify({"id": manager.id, "name": user.display_name})


@app.route("/api/board/<int:board_id>/lehrer/<int:manager_id>", methods=["DELETE"])
@login_required
@admin_required
def api_remove_board_lehrer(board_id, manager_id):
    manager = db.session.get(BoardManager, manager_id) or abort(404)
    if manager.board_id != board_id:
        abort(404)
    name = manager.user.display_name if manager.user else "Unbekannt"
    log_action(board_id, "Lehrkraft-Zugriff entzogen", name)
    db.session.delete(manager)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/board/<int:board_id>/date", methods=["POST"])
@login_required
@staff_required
def api_add_date(board_id):
    db.session.get(Board, board_id) or abort(404)
    data = request.json or {}
    raw = data.get("date", "")
    label = (data.get("label") or "").strip()[:60]
    try:
        d = date_cls.fromisoformat(raw)
    except ValueError:
        return jsonify({"error": "Ungültiges Datum"}), 400
    # Mehrere Termine am selben Tag sind erlaubt (z.B. zwei Proben am gleichen Tag).
    # Nur exakte Duplikate (gleiches Datum + gleiche Notiz) werden abgefangen.
    if EventDate.query.filter_by(board_id=board_id, date=d, label=(label or None)).first():
        return jsonify({"error": "Dieser Termin mit gleicher Notiz existiert an diesem Tag bereits"}), 400
    event_date = EventDate(board_id=board_id, date=d, label=label or None)
    db.session.add(event_date)
    detail = d.isoformat() + (f" – {label}" if label else "")
    log_action(board_id, "Termin hinzugefügt", detail)
    db.session.commit()
    return jsonify({"id": event_date.id, "date": d.isoformat(), "label": label})


@app.route("/api/date/<int:date_id>", methods=["DELETE"])
@login_required
@staff_required
def api_delete_date(date_id):
    event_date = db.session.get(EventDate, date_id) or abort(404)
    board_id = event_date.board_id
    log_action(board_id, "Termin entfernt", event_date.date.isoformat())
    db.session.delete(event_date)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/date/<int:date_id>/attachment", methods=["POST"])
@login_required
@staff_required
def api_upload_attachment(date_id):
    event_date = db.session.get(EventDate, date_id) or abort(404)
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Keine Datei ausgewählt"}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Nur PDF-Dateien sind erlaubt"}), 400

    data = file.read()
    if len(data) > 10 * 1024 * 1024:
        return jsonify({"error": "Datei ist zu groß (max. 10 MB)"}), 400

    event_date.attachment_filename = file.filename[:255]
    event_date.attachment_data = data
    event_date.attachment_uploaded_at = datetime.utcnow()
    event_date.attachment_uploaded_by = current_user.id
    log_action(event_date.board_id, "PDF angehängt", f"{event_date.date.isoformat()} – {file.filename}")
    db.session.commit()
    return jsonify({"ok": True, "filename": event_date.attachment_filename})


@app.route("/api/date/<int:date_id>/attachment", methods=["DELETE"])
@login_required
@staff_required
def api_delete_attachment(date_id):
    event_date = db.session.get(EventDate, date_id) or abort(404)
    old_name = event_date.attachment_filename
    event_date.attachment_filename = None
    event_date.attachment_data = None
    event_date.attachment_uploaded_at = None
    event_date.attachment_uploaded_by = None
    log_action(event_date.board_id, "PDF entfernt", f"{event_date.date.isoformat()} – {old_name}" if old_name else event_date.date.isoformat())
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/date/<int:date_id>/attachment")
@login_required
def download_attachment(date_id):
    event_date = db.session.get(EventDate, date_id) or abort(404)
    board = db.session.get(Board, event_date.board_id) or abort(404)

    if not current_user.is_staff:
        my_person = Person.query.filter_by(board_id=board.id, user_id=current_user.id).first()
        if not my_person:
            abort(403)

    if not event_date.attachment_data:
        abort(404)

    return send_file(
        BytesIO(event_date.attachment_data),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=event_date.attachment_filename or "anhang.pdf",
    )


@app.route("/api/entry", methods=["POST"])
@login_required
def api_set_entry():
    data = request.json or {}
    person_id = data.get("person_id")
    date_id = data.get("date_id")
    status = data.get("status")

    if status not in STATUS_VALUES:
        return jsonify({"error": "Ungültiger Status"}), 400

    person = db.session.get(Person, person_id) or abort(404)
    event_date = db.session.get(EventDate, date_id) or abort(404)

    if not can_edit_person(person):
        abort(403)

    entry = Entry.query.filter_by(person_id=person_id, date_id=date_id).first()
    if not entry:
        entry = Entry(board_id=person.board_id, person_id=person_id, date_id=date_id, status="unset")
        db.session.add(entry)

    entry.status = status
    entry.updated_by = current_user.id
    entry.updated_at = datetime.utcnow()

    log_action(
        person.board_id,
        "Status geändert",
        f"{person.name} / {event_date.date.isoformat()} -> {STATUS_LABEL.get(status, status)}",
    )
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# PDF Export
# ---------------------------------------------------------------------------

@app.route("/board/<int:board_id>/export.pdf")
@login_required
@staff_required
def export_pdf(board_id):
    board = db.session.get(Board, board_id) or abort(404)
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.pdfgen import canvas as pdfcanvas

    people = [p for p in board.people if not p.user or p.user.role != "lehrer"]
    dates = board.dates
    entries = {}
    for e in Entry.query.filter_by(board_id=board_id).all():
        entries[(e.person_id, e.date_id)] = e.status

    ACCENT = colors.HexColor("#3D4CE0")
    INK = colors.HexColor("#171A21")
    INK_SOFT = colors.HexColor("#5B6270")
    LINE = colors.HexColor("#E6E8EC")
    HEADER_BG = colors.HexColor("#F3F4F9")

    STATUS_META = {
        "ok":    ("Verfügbar",       "OK",   colors.HexColor("#1B9E5A"), colors.HexColor("#E4F6EC")),
        "warn":  ("Mit Vorbehalt",   "VB",   colors.HexColor("#D9822B"), colors.HexColor("#FCEFDD")),
        "no":    ("Nicht verfügbar", "NEIN", colors.HexColor("#D6455D"), colors.HexColor("#FBE7EB")),
        "unset": ("Offen",           "-",    colors.HexColor("#9AA0AC"), colors.HexColor("#F2F3F5")),
    }

    generated_at = datetime.now().strftime("%d.%m.%Y, %H:%M Uhr")
    LM = 14 * mm  # linker/rechter Rand, deckt sich mit Dokumentrand

    class ChromeCanvas(pdfcanvas.Canvas):
        """Zeichnet Kopfbanner, Legende und Fußzeile mit Seitenzahl auf jede Seite."""

        def __init__(self, *args, **kwargs):
            pdfcanvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_chrome(total)
                pdfcanvas.Canvas.showPage(self)
            pdfcanvas.Canvas.save(self)

        def _draw_chrome(self, total_pages):
            width, height = self._pagesize

            # Kopfbanner
            self.setFillColor(ACCENT)
            self.rect(0, height - 60, width, 60, fill=1, stroke=0)
            self.setFillColor(colors.white)
            self.setFont("Helvetica-Bold", 16)
            self.drawString(LM, height - 30, board.name)
            self.setFont("Helvetica", 9)
            self.drawString(LM, height - 44, board.description or "Verfügbarkeitsübersicht")
            self.setFont("Helvetica", 8)
            self.drawRightString(width - LM, height - 27, "Erstellt am")
            self.drawRightString(width - LM, height - 39, generated_at)

            # Legende
            legend_y = height - 76
            x = LM
            for key in ("ok", "warn", "no", "unset"):
                label, _, text_color, bg_color = STATUS_META[key]
                self.setFillColor(bg_color)
                self.roundRect(x, legend_y - 3, 10, 10, 2, fill=1, stroke=0)
                self.setFillColor(INK_SOFT)
                self.setFont("Helvetica", 8.5)
                self.drawString(x + 15, legend_y, label)
                x += 15 + self.stringWidth(label, "Helvetica", 8.5) + 20

            # Fusszeile
            self.setStrokeColor(LINE)
            self.setLineWidth(0.6)
            self.line(LM, 15 * mm, width - LM, 15 * mm)
            self.setFont("Helvetica", 8)
            self.setFillColor(INK_SOFT)
            self.drawString(LM, 10 * mm, "Verfügbarkeitsplaner")
            self.drawRightString(width - LM, 10 * mm, f"Seite {self.getPageNumber()} von {total_pages}")

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=LM, rightMargin=LM, topMargin=34 * mm, bottomMargin=20 * mm,
    )

    header_style = ParagraphStyle(
        name="DateHeader", fontName="Helvetica-Bold", fontSize=9.5,
        alignment=TA_CENTER, textColor=INK, leading=12,
    )

    def date_header_cell(d):
        date_str = d.date.strftime("%d.%m.%Y")
        if d.label:
            safe_label = (d.label or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return Paragraph(f"{date_str}<br/><font size=7.5 color='#5B6270'>{safe_label}</font>", header_style)
        return Paragraph(date_str, header_style)

    header_row = ["Name"] + [date_header_cell(d) for d in dates]
    rows = [header_row]
    for p in people:
        row = [p.name]
        for d in dates:
            status = entries.get((p.id, d.id), "unset")
            row.append(STATUS_META[status][1])
        rows.append(row)

    usable_width = landscape(A4)[0] - 2 * LM
    name_col = 48 * mm
    col_widths = [name_col] + [max(20 * mm, (usable_width - name_col) / max(len(dates), 1))] * len(dates)
    table = Table(rows, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 1), (0, -1), INK),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.6, LINE),
        ("LINEBELOW", (0, 0), (-1, 0), 1, ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (0, -1), [colors.white, colors.HexColor("#FBFBFC")]),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (0, -1), 10),
    ]
    for r, p in enumerate(people, start=1):
        for c, d in enumerate(dates, start=1):
            status = entries.get((p.id, d.id), "unset")
            style_cmds.append(("BACKGROUND", (c, r), (c, r), STATUS_META[status][3]))
            style_cmds.append(("TEXTCOLOR", (c, r), (c, r), STATUS_META[status][2]))
            style_cmds.append(("FONTNAME", (c, r), (c, r), "Helvetica-Bold"))

    table.setStyle(TableStyle(style_cmds))

    doc.build([table], canvasmaker=ChromeCanvas)
    buf.seek(0)

    filename = f"{board.name.replace(' ', '_')}.pdf"
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.route("/admin")
@login_required
@admin_required
def admin_home():
    users = User.query.order_by(User.created_at).all()
    boards = Board.query.order_by(Board.created_at.desc()).all()
    invites = Invite.query.order_by(Invite.created_at.desc()).all()
    access_requests = AccessRequest.query.order_by(AccessRequest.requested_at.desc()).all()
    return render_template("admin.html", users=users, boards=boards, invites=invites, access_requests=access_requests)


@app.route("/admin/access-request/<int:req_id>/resolve", methods=["POST"])
@login_required
@admin_required
def admin_resolve_access_request(req_id):
    req = db.session.get(AccessRequest, req_id) or abort(404)
    db.session.delete(req)
    db.session.commit()
    flash("Anfrage als erledigt markiert.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/invite", methods=["POST"])
@login_required
@admin_required
def admin_create_invite():
    label = request.form.get("label", "").strip()
    max_uses = int(request.form.get("max_uses", 1) or 0)
    expires_days = int(request.form.get("expires_days", 0) or 0)

    invite = Invite(
        code=secrets.token_urlsafe(9),
        label=label[:80] or None,
        created_by=current_user.id,
        max_uses=max_uses,
        expires_at=(datetime.utcnow() + timedelta(days=expires_days)) if expires_days else None,
    )
    db.session.add(invite)
    db.session.commit()
    flash("Einladungslink wurde erstellt.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/invite/<int:invite_id>/revoke", methods=["POST"])
@login_required
@admin_required
def admin_revoke_invite(invite_id):
    invite = db.session.get(Invite, invite_id) or abort(404)
    invite.active = False
    db.session.commit()
    flash("Einladungslink wurde widerrufen.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/board", methods=["POST"])
@login_required
@admin_required
def admin_create_board():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    if not name:
        flash("Bitte einen Namen für das Board angeben.", "error")
        return redirect(url_for("admin_home"))
    board = Board(name=name[:80], description=description[:240], created_by=current_user.id)
    db.session.add(board)
    db.session.commit()
    flash(f"Board „{name}“ wurde angelegt.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/board/<int:board_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_board(board_id):
    board = db.session.get(Board, board_id) or abort(404)
    db.session.delete(board)
    db.session.commit()
    flash("Board wurde gelöscht.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/user/<int:user_id>/rename", methods=["POST"])
@login_required
@admin_required
def admin_rename_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    display_name = request.form.get("display_name", "").strip()
    if display_name:
        user.display_name = display_name[:60]
        db.session.commit()
        flash("Anzeigename aktualisiert.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/user/<int:user_id>/role", methods=["POST"])
@login_required
@admin_required
def admin_set_role(user_id):
    user = db.session.get(User, user_id) or abort(404)
    new_role = request.form.get("role")
    if new_role not in ("admin", "lehrer", "user"):
        abort(400)
    if user.id == current_user.id and new_role != "admin":
        flash("Du kannst dir selbst nicht die Admin-Rechte entziehen.", "error")
        return redirect(url_for("admin_home"))
    user.role = new_role
    db.session.commit()
    flash(f"Rolle von {user.display_name} wurde geändert.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash("Du kannst dich nicht selbst löschen.", "error")
        return redirect(url_for("admin_home"))

    # Alle Spuren des Nutzers entfernen - inklusive seiner Zeilen in Boards (nicht als Gast belassen).
    # Einzeln über die ORM löschen (nicht als Bulk-Query), damit die Kaskade zu den
    # zugehörigen Verfügbarkeits-Einträgen greift und keine verwaisten Datensätze zurückbleiben.
    for person in Person.query.filter_by(user_id=user.id).all():
        db.session.delete(person)
    Entry.query.filter_by(updated_by=user.id).update({"updated_by": None})
    ChangeLog.query.filter_by(user_id=user.id).update({"user_id": None})
    Board.query.filter_by(created_by=user.id).update({"created_by": None})
    Invite.query.filter_by(created_by=user.id).update({"created_by": None})
    BoardManager.query.filter_by(user_id=user.id).delete()

    db.session.delete(user)
    db.session.commit()
    flash("Nutzer und alle seine Board-Einträge wurden vollständig gelöscht.", "success")
    return redirect(url_for("admin_home"))


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

@app.cli.command("init-db")
def init_db():
    with app.app_context():
        db.create_all()
    print("Datenbank initialisiert.")


# JSON-API-Routen sind zustandsändernd per fetch() statt HTML-Formular.
# Sie tragen keinen CSRF-Token, sind aber durch SameSite=Lax-Session-Cookies
# bereits gegen Cross-Site-Anfragen abgesichert (Browser senden solche Cookies
# nicht bei Cross-Origin-fetch/XHR-Requests).
for view_name in (
    "api_add_person", "api_rename_person", "api_delete_person",
    "api_add_date", "api_delete_date", "api_set_entry",
    "api_upload_attachment", "api_delete_attachment",
    "api_add_board_lehrer", "api_remove_board_lehrer",
):
    csrf.exempt(app.view_functions[view_name])

def run_light_migrations():
    """Ergänzt fehlende Spalten in bereits bestehenden Tabellen, ohne Daten anzufassen.
    db.create_all() legt nur neue Tabellen an, ändert aber keine existierenden - das holt das hier nach."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    if "event_dates" not in inspector.get_table_names():
        return
    existing_cols = {c["name"] for c in inspector.get_columns("event_dates")}
    new_columns = {
        "attachment_filename": db.String(255),
        "attachment_data": db.LargeBinary(),
        "attachment_uploaded_at": db.DateTime(),
        "attachment_uploaded_by": db.Integer(),
    }
    with db.engine.begin() as conn:
        for col_name, col_type in new_columns.items():
            if col_name not in existing_cols:
                type_sql = col_type.compile(dialect=db.engine.dialect)
                conn.execute(text(f"ALTER TABLE event_dates ADD COLUMN {col_name} {type_sql}"))


with app.app_context():
    db.create_all()
    run_light_migrations()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
