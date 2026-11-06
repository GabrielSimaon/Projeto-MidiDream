"""Identidade das faixas: reconhecimento de nomes não substitui o detector de áudio."""
from pathlib import Path

EXTRA = {
    "accordion": (21, "Accordion", 5),
    "acoustic_guitar": (25, "Acoustic Guitar", 6),
    "electric_guitar": (27, "Electric Guitar", 7),
}


def extra_kind(name):
    name = str(name).casefold()
    if any(word in name for word in ("accordion", "sanfona", "acordeon", "acordeão")):
        return "accordion"
    if "acoustic" in name:
        return "acoustic_guitar"
    if "electric_guitar" in name or "electric guitar" in name:
        return "electric_guitar"
    return None


def classify_name(name):
    extra = extra_kind(name)
    if extra:
        return extra
    low = Path(name).stem.casefold()
    for kind, words in (("metronome", ("metro", "click")), ("drums", ("drum", "bateria", "percuss")),
                        ("bass", ("bass", "baixo")), ("vocals", ("vocal", "voice", "voz")),
                        ("guitar", ("guitar", "viol", "guitarra")), ("piano", ("piano", "keys", "teclado"))):
        if any(word in low for word in words):
            return kind
    return "other"
