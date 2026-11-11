"""Identidade das faixas: reconhecimento de nomes não substitui o detector de áudio."""
from pathlib import Path

EXTRA = {
    "accordion": (21, "Accordion", 5),
    "acoustic_guitar": (25, "Acoustic Guitar", 3),
    "electric_guitar": (27, "Electric Guitar", 6),
    "piano_solo": (0, "Piano Solo", 8),
    "guitar_solo": (27, "Electric Guitar Solo", 8),
    "acoustic_solo": (25, "Acoustic Guitar Solo", 8),
    "pad": (88, "Synth Pad", 4),
    "strings": (48, "Strings", 10),
    "clarinet": (71, "Clarinet", 11),
    "sax": (66, "Tenor Sax", 12),
}


def extra_kind(name):
    name = str(name).casefold()
    if any(word in name for word in ("accordion", "sanfona", "acordeon", "acordeão")):
        return "accordion"
    if "solo" in name:
        if any(k in name for k in ("guitarra", "guitar", "eletrica", "elétrica")):
            return "guitar_solo"
        if any(k in name for k in ("violao", "violão", "acoustic")):
            return "acoustic_solo"
        return "piano_solo"
    if "acoustic" in name:
        return "acoustic_guitar"
    if "electric_guitar" in name or "electric guitar" in name or "guitarra elétrica" in name:
        return "electric_guitar"
    if name in ("piano solo", "pianosolo", "piano_solo"):
        return "piano_solo"
    if any(word in name for word in ("pad", "synth pad", "cama")):
        return "pad"
    if any(word in name for word in ("string", "cordas", "ensemble")):
        return "strings"
    if any(word in name for word in ("clarinet", "clarinete")):
        return "clarinet"
    if any(word in name for word in ("sax", "saxofone")):
        return "sax"
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
