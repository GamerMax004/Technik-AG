import os
import re

_BLACKLIST_PATH = os.path.join(os.path.dirname(__file__), "banned_words.txt")
_LEET_MAP = str.maketrans({"4": "a", "3": "e", "1": "i", "0": "o", "5": "s", "$": "s", "@": "a"})


def _load_words():
    """Liest banned_words.txt und trennt nach Abschnitt.
    Gibt (general_words, reserved_words) zurück."""
    general, reserved = [], []
    section = "general"
    try:
        with open(_BLACKLIST_PATH, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("## SECTION:"):
                    section = line.split(":", 1)[1].strip().lower()
                    continue
                if line.startswith("#"):
                    continue
                word = line.lower()
                (reserved if section == "reserved" else general).append(word)
    except FileNotFoundError:
        pass
    return general, reserved


def _normalize(text):
    return text.strip().lower().translate(_LEET_MAP)


def _matches_any(normalized, words):
    for word in words:
        if word.isdigit():
            if re.search(rf"(?<!\d){re.escape(word)}(?!\d)", normalized):
                return True
        elif word in normalized:
            return True
    return False


def contains_banned_word(text, include_reserved=False):
    """True, wenn der Text einen gesperrten Begriff enthält.
    include_reserved=True prüft zusätzlich reservierte Systembegriffe
    (z.B. "admin") - das ist nur für den Login-Benutzernamen gedacht,
    nicht für den frei wählbaren Anzeigenamen."""
    if not text:
        return False
    normalized = _normalize(text)
    general, reserved = _load_words()
    if _matches_any(normalized, general):
        return True
    if include_reserved and _matches_any(normalized, reserved):
        return True
    return False
