"""Perfil de articulação e leitura estatística de referência: não copia notas/letra."""

import copy
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
import pretty_midi
import mido


def role(name, program=0, drum=False):
    """Identifica o grupo do instrumento pelo nome da faixa e pela indicação de bateria."""
    from instrument_catalog import extra_kind
    extra = extra_kind(name)
    if extra:
        return extra
    name = name.casefold()
    if drum or "drum" in name:
        return "drums"
    if "bass" in name or "baixo" in name:
        return "bass"
    if any(x in name for x in ("choir", "vocal", "voice", "voz", "flute")):
        return "vocals"
    if "piano" in name or "teclado" in name:
        return "piano"
    return "guitar"


def read_reference(path):
    """Lê estatísticas de um MIDI de referência, sem copiar sua sequência de notas."""
    m = mido.MidiFile(path, charset="latin1")
    pm = pretty_midi.PrettyMIDI(str(path))
    tempos = {x.tempo for t in m.tracks for x in t if x.type == "set_tempo"}
    bpm = float(mido.tempo2bpm(next(iter(tempos)))) if len(tempos) == 1 else None
    stats = {}
    pitches = Counter()
    residues = Counter()
    step = m.ticks_per_beat / 4
    for inst in pm.instruments:
        if not inst.notes:
            continue
        group = role(inst.name, inst.program, inst.is_drum)
        if group not in stats:
            stats[group] = {"program": int(inst.program), "velocity": [], "duration": []}
        stats[group]["velocity"].extend(int(n.velocity) for n in inst.notes)
        stats[group]["duration"].extend(float(n.end - n.start) for n in inst.notes)
        if inst.is_drum:
            pieces = defaultdict(list)
            for n in inst.notes:
                pieces[str(n.pitch)].append(n.velocity)
            stats[group]["pieces"] = {
                p: np.percentile(v, [10, 50, 90]).tolist() for p, v in pieces.items()
            }
        else:
            pitches.update(int(n.pitch) % 12 for n in inst.notes)
    for track in m.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity and msg.channel != 9:
                residues[round((tick % step) / step, 3)] += 1
    for data in stats.values():
        data["velocity"] = np.percentile(data["velocity"], [10, 50, 90]).tolist()
        data["median_duration"] = float(np.median(data.pop("duration")))
    # Só usa sete classes quando o próprio MIDI está quase todo dentro delas.
    top = pitches.most_common(7)
    scale = (
        sorted(p for p, _ in top)
        if len(top) == 7 and sum(c for _, c in top) / max(1, sum(pitches.values())) >= 0.98
        else []
    )
    if scale and not any(
        {(tonic + i) % 12 for i in (0, 2, 4, 5, 7, 9, 11)} == set(scale) for tonic in range(12)
    ):
        scale = []
    phase, count = residues.most_common(1)[0] if residues else (0, 0)
    grid_confidence = count / max(1, sum(residues.values()))
    anchor = float(phase * 60 / bpm / 4) if bpm and grid_confidence >= 0.9 else None
    return {
        "bpm": bpm,
        "grid_anchor": anchor,
        "grid_confidence": grid_confidence,
        "scale": scale,
        "stats": stats,
        "compact": "piano" not in stats,
        "name": Path(path).name,
        "merge_gap": 0.08,
    }


def apply_style(midi, stem, bpm, grid_times, grid=16, profile=None):
    """Reúne fragmentos de mesma altura e aplica somente parâmetros agregados."""
    profile = profile or DEFAULT_STYLE
    result = copy.deepcopy(midi)
    gap = float(profile.get("merge_gap", 0.015))
    stats = profile.get("stats", {}).get(stem, {})
    if stem == "vocals":
        for inst in result.instruments:
            if stats.get("velocity"):
                match_velocity(inst.notes, stats["velocity"])
            if "program" in stats:
                inst.program = int(stats["program"])
        return result
    step = 60 / bpm * 4 / grid if grid else 0
    for inst in result.instruments:
        if inst.is_drum:
            continue
        # As alturas são corrigidas depois, com cifras e contexto temporal.
        # Evita uma correção global cega antes de decidir quais notas são fracas.
        merged = []
        for n in sorted(inst.notes, key=lambda n: (n.pitch, n.start)):
            if (
                merged
                and n.pitch == merged[-1].pitch
                and n.start - merged[-1].end <= gap
                and not (n.start > merged[-1].end and n.velocity > merged[-1].velocity * 1.15)
            ):
                merged[-1].end = max(merged[-1].end, n.end)
                merged[-1].velocity = max(merged[-1].velocity, n.velocity)
            else:
                if merged and merged[-1].pitch == n.pitch and merged[-1].end > n.start:
                    merged[-1].end = max(merged[-1].start + 0.02, n.start)
                merged.append(n)
        # Limites por polifonia não são aplicados neste perfil: instrumentos dobrados
        # e acordes sustentados não são necessariamente falsos.
        if grid and len(grid_times):

            def snap_time_to_grid(time, times, unused_step):
                index = int(np.searchsorted(times, time))
                candidates = times[max(0, index - 1) : min(len(times), index + 1)]
                return (
                    float(min(candidates, key=lambda t: abs(t - time))) if len(candidates) else time
                )

            for n in merged:
                n.start = max(0.0, snap_time_to_grid(n.start, grid_times, step))
                n.end = max(n.start + min(0.05, step), snap_time_to_grid(n.end, grid_times, step))
        if stats.get("velocity") and merged:
            match_velocity(merged, stats["velocity"])
        if "program" in stats:
            inst.program = int(stats["program"])
        inst.notes = sorted(merged, key=lambda n: (n.start, n.pitch))
    return result


# Apenas timbres e dinâmica agregada; sem BPM, escala ou notas de uma música fixa.
DEFAULT_STYLE = {
    "merge_gap": 0.015,
    "stats": {
        "bass": {"program": 33, "velocity": [30, 37, 118]},
        "guitar": {"program": 25, "velocity": [16, 18, 57]},
        "vocals": {"program": 52, "velocity": [20, 26, 84]},
        "drums": {
            "pieces": {
                "36": [122, 127, 127],
                "38": [105, 113, 115],
                "42": [75, 85, 95],
                "49": [90, 105, 112],
            }
        },
    },
}


def match_velocity(notes, target):
    """Ajusta a intensidade das notas à curva de referência; modifica a lista recebida."""
    if not notes:
        return
    source = np.percentile([n.velocity for n in notes], [10, 50, 90])
    if source[-1] == source[0]:
        for n in notes:
            n.velocity = int(np.clip(target[1], 1, 127))
        return
    unique = np.unique(source)
    values = [float(np.mean(np.asarray(target)[source == point])) for point in unique]
    for n in notes:
        n.velocity = int(np.clip(np.interp(n.velocity, unique, values), 1, 127))


def style_drums(midi, profile):
    """Aplica a dinâmica de referência separadamente ao bumbo, caixa e pratos."""
    specs = profile.get("stats", {}).get("drums", {}).get("pieces", {})
    for inst in midi.instruments:
        for pitch in {n.pitch for n in inst.notes}:
            if str(pitch) in specs:
                match_velocity([n for n in inst.notes if n.pitch == pitch], specs[str(pitch)])
    return midi
