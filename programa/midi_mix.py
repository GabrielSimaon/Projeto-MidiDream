"""Mixagem MIDI reversível: volume de canal e intensidade das notas (não ganho de áudio)."""

import math
import shutil
from pathlib import Path
import mido

DEFAULT_MIX = {
    "bass": 115,
    "drums": 112,
    "kick": 110,
    "snare": 110,
    "toms": 100,
    "hihat": 110,
    "cymbals": 110,
    "piano": 105,
    "piano_solo": 115,
    "other": 70,
    "punch": 70,
    "mute_other": False,
    "piano_program": 0,
}
# Piso de intensidade do "punch": notas fracas do baixo/bumbo sobem até esse nível.
PUNCH_FLOOR = {"bass": 82, "kick": 112}
PIECES = {
    "kick": {35, 36},
    "snare": {38, 40},
    "toms": {41, 43, 45, 47, 48, 50},
    "hihat": {42, 44, 46},
    "cymbals": {49, 51, 52, 53, 55, 57, 59},
}


def validate_mix(settings=None):
    settings = dict(settings or {})
    # Resultados antigos guardam só "mute_other": True/False; convertemos para o nível.
    if "other" not in settings and "mute_other" in settings:
        settings["other"] = 0 if settings["mute_other"] else 100
    values = {**DEFAULT_MIX, **settings}
    for name in ("bass", "drums", "kick", "snare", "toms", "hihat", "cymbals", "other", "piano", "piano_solo"):
        if name in values:
            value = float(values[name])
            if not math.isfinite(value) or not 0 <= value <= 150:
                raise ValueError("Use intensidades entre 0 e 150%.")
            values[name] = round(value)
    punch = float(values["punch"])
    if not math.isfinite(punch) or not 0 <= punch <= 100:
        raise ValueError("A pressão do baixo e do bumbo vai de 0 a 100%.")
    values["punch"] = round(punch)
    if values["piano_program"] not in (0, 4, 16, 21):
        raise ValueError("Timbre de teclado inválido.")
    values["mute_other"] = values["other"] == 0
    return values


def _punch(velocity, floor, amount):
    """Puxa a intensidade para uma faixa mais firme (compressão + piso), sem passar de 127."""
    target = floor + (127 - floor) * velocity / 127
    return round(velocity + (target - velocity) * amount / 100)


def mix_midi(midi, settings):
    values = validate_mix(settings)
    for track in midi.tracks:
        channels = {msg.channel for msg in track if not msg.is_meta and hasattr(msg, "channel")}
        desired = {}
        if 0 in channels:
            desired[0] = min(127, round(100 * values["bass"] / 100))
        if 9 in channels:
            desired[9] = min(127, round(100 * values["drums"] / 100))
        if 8 in channels:
            desired[8] = min(127, round(100 * values.get("piano_solo", 115) / 100))
        for ch in channels & {2, 5}:
            desired[ch] = min(127, round(100 * values.get("piano", 105) / 100))
        for ch in channels & {4, 6, 7}:
            desired[ch] = 0 if values["other"] == 0 else min(127, round(100 * values["other"] / 100))
        existing = set()
        for msg in track:
            if msg.is_meta:
                continue
            if msg.type == "control_change" and msg.control == 7 and msg.channel in desired:
                val_pct = (
                    values["bass"]
                    if msg.channel == 0
                    else (
                        values["drums"]
                        if msg.channel == 9
                        else (
                            values.get("piano_solo", 115)
                            if msg.channel == 8
                            else (
                                values.get("piano", 105)
                                if msg.channel in (2, 5)
                                else values.get("other", 70)
                            )
                        )
                    )
                )
                msg.value = (
                    min(127, round(msg.value * val_pct / 100))
                    if msg.channel not in (4, 6, 7) or values["other"] > 0
                    else 0
                )
                existing.add(msg.channel)
            if msg.type == "program_change" and msg.channel in (2, 5, 8):
                msg.program = values["piano_program"]
            if msg.type == "note_on" and msg.velocity > 0:
                gain = 1.0
                if msg.channel == 0:
                    msg.velocity = _punch(msg.velocity, PUNCH_FLOOR["bass"], values["punch"])
                elif msg.channel == 9 and msg.note in PIECES["kick"]:
                    msg.velocity = _punch(msg.velocity, PUNCH_FLOOR["kick"], values["punch"])
                if msg.channel == 0:
                    gain = 1 + (values["bass"] - 100) / 200
                elif msg.channel == 8:
                    gain = values.get("piano_solo", 115) / 100
                elif msg.channel in (2, 5):
                    gain = values.get("piano", 105) / 100
                elif msg.channel in (4, 6, 7):
                    gain = values["other"] / 100 if values["other"] else 1.0  # 0% = mudo via CC7
                elif msg.channel == 9:
                    gain = 1 + (values["drums"] - 100) / 200
                    for piece, notes in PIECES.items():
                        if msg.note in notes:
                            gain *= values[piece] / 100
                            break
                # 0% silencia via CC7, mantendo Note Off e integridade temporal.
                msg.velocity = max(0, min(127, round(msg.velocity * max(0, gain))))
        for channel, volume in desired.items():
            if channel not in existing:
                track.insert(
                    0,
                    mido.Message(
                        "control_change", channel=channel, control=7, value=volume, time=0
                    ),
                )
    return midi


def apply_mix(result_dir, settings):
    """Cada ajuste parte da cópia original, sem acumular ganho ou alterar os tempos."""
    values = validate_mix(settings)
    root = Path(result_dir)
    midi_dir, backup = root / "midi", root / "midi_originais"
    backup.mkdir(exist_ok=True)
    names = {
        p.name
        for folder in (midi_dir, backup)
        for p in folder.glob("*")
        if p.suffix.lower() in (".mid", ".kar")
    }
    for name in sorted(names):
        file = midi_dir / name
        original = backup / name
        if not original.exists():
            shutil.copy2(file, original)
        midi = mido.MidiFile(original, charset="latin1")
        mix_midi(midi, values)
        temporary = file.with_suffix(file.suffix + ".tmp")
        midi.save(temporary)
        temporary.replace(file)
    return values
