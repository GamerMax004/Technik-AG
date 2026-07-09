from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

STATUS_VALUES = ("unset", "ok", "warn", "no")


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(60), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")  # 'admin' | 'lehrer' | 'user'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_staff(self):
        """Admin oder Lehrer: darf Board-Inhalte verwalten und PDFs exportieren."""
        return self.role in ("admin", "lehrer")


ROLE_LABELS = {"admin": "Administrator", "lehrer": "Lehrer", "user": "Nutzer"}


class Board(db.Model):
    __tablename__ = "boards"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(240))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    people = db.relationship("Person", backref="board", cascade="all, delete-orphan", order_by="Person.sort_order")
    dates = db.relationship("EventDate", backref="board", cascade="all, delete-orphan", order_by="EventDate.date")
    logs = db.relationship("ChangeLog", backref="board", cascade="all, delete-orphan", order_by="ChangeLog.timestamp.desc()")


class Person(db.Model):
    __tablename__ = "people"

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey("boards.id"), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    # Verknüpfung zum Nutzerkonto: nur der verknüpfte Nutzer (oder Staff) darf diese Zeile bearbeiten.
    # NULL = "Gastzeile" ohne eigenes Konto, nur von Admin/Lehrer bearbeitbar.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    entries = db.relationship("Entry", backref="person", cascade="all, delete-orphan")
    user = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("board_id", "user_id", name="uix_board_user"),)


class EventDate(db.Model):
    __tablename__ = "event_dates"

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey("boards.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    label = db.Column(db.String(60))

    # Optionaler PDF-Anhang, direkt in der Datenbank gespeichert (Render-Dateisystem ist nicht dauerhaft)
    attachment_filename = db.Column(db.String(255))
    attachment_data = db.Column(db.LargeBinary)
    attachment_uploaded_at = db.Column(db.DateTime)
    attachment_uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    entries = db.relationship("Entry", backref="event_date", cascade="all, delete-orphan")


class Entry(db.Model):
    __tablename__ = "entries"

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey("boards.id"), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False)
    date_id = db.Column(db.Integer, db.ForeignKey("event_dates.id"), nullable=False)
    status = db.Column(db.String(10), nullable=False, default="unset")
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("person_id", "date_id", name="uix_person_date"),)


class Invite(db.Model):
    __tablename__ = "invites"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    label = db.Column(db.String(80))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)
    max_uses = db.Column(db.Integer, default=1)  # 0 = unbegrenzt
    uses_count = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)

    def is_valid(self):
        if not self.active:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        if self.max_uses and self.uses_count >= self.max_uses:
            return False
        return True


class ChangeLog(db.Model):
    __tablename__ = "change_logs"

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey("boards.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(120), nullable=False)
    detail = db.Column(db.String(240))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
