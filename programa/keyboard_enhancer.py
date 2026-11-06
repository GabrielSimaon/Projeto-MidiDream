"""Aprimoramento harmônico e rítmico dos teclados (piano.mid e other.mid).

Atende às diretrizes musicais do usuário:
1. Afinação estrita com as cifras ativas da música (elimina notas desafinadas).
2. Marcação e ênfase rítmica no tempo da música para guiar o canto.
3. Piano (piano.mid) atua na marcação rítmica (pulsação clara nas batidas).
4. Outros teclados (other.mid) fornecem a cama harmônica / sustentação suave.
5. Permite cantar por cima com harmonia consonante e andamento firme.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Sequence

import numpy as np
import pretty_midi

from midi_timing import make_instrumental
from music_guide import chord_notes, chord_root_and_bass
from accompaniment_cleanup import key_scale


def _get_chord_intervals(
    chords: Sequence[tuple[float, str]], duration: float, default_key: str | None = None
) -> list[dict]:
    """Constrói os intervalos temporais de cada acorde com suas notas constitutivas e escala."""
    scale = key_scale(default_key) if default_key else {0, 2, 4, 5, 7, 9, 11}
    if not chords:
        return [
            {
                "start": 0.0,
                "end": max(duration, 60.0),
                "symbol": default_key or "C",
                "notes": scale,
                "scale": scale,
                "root": min(scale),
                "bass": min(scale),
            }
        ]

    intervals = []
    events = sorted(chords, key=lambda c: c[0])
    for idx, (t_start, symbol) in enumerate(events):
        nxt_start = events[idx + 1][0] if idx + 1 < len(events) else max(duration, t_start + 4.0)
        try:
            c_notes = {n % 12 for n in chord_notes(symbol)}
            root_pc, bass_pc = chord_root_and_bass(symbol)
        except Exception:
            c_notes = {0, 4, 7}
            root_pc, bass_pc = 0, 0
        intervals.append(
            {
                "start": float(t_start),
                "end": float(nxt_start),
                "symbol": symbol,
                "notes": c_notes,
                "scale": scale,
                "root": root_pc,
                "bass": bass_pc,
            }
        )
    return intervals


def _find_active_interval(intervals: list[dict], time_sec: float) -> dict:
    """Localiza o acorde ativo para o instante de tempo."""
    if not intervals:
        return {"notes": {0, 4, 7}, "scale": {0, 2, 4, 5, 7, 9, 11}, "root": 0, "bass": 0, "symbol": "C"}
    for iv in reversed(intervals):
        if iv["start"] <= time_sec + 0.03:
            return iv
    return intervals[0]


def _chord_voicing(chord_pcs: set[int], low_midi: int = 60, high_midi: int = 76) -> list[int]:
    """Gera um voicing consonante fechado na tessitura desejada."""
    voicing = []
    for pc in sorted(chord_pcs):
        for oct_shift in (3, 4, 5, 6):
            pitch = pc + 12 * oct_shift
            if low_midi <= pitch <= high_midi:
                voicing.append(pitch)
    voicing = sorted(set(voicing))
    if len(voicing) > 3:
        # Mantém até 3 notas para maior clareza rítmica
        voicing = [voicing[0], voicing[1], voicing[-1]]
    if not voicing:
        root = min(chord_pcs) if chord_pcs else 0
        voicing = [root + 60, (root + 4) % 12 + 60, (root + 7) % 12 + 60]
    return voicing


def enhance_piano_marking(
    inst: pretty_midi.Instrument,
    intervals: list[dict],
    bpm: float = 120.0,
    duration: float = 0.0,
) -> pretty_midi.Instrument:
    """Transforma o piano em uma marcação rítmica afinada, limpa e com ênfase no tempo."""
    step = 60.0 / float(bpm)
    step_16th = step / 4.0
    half_step = step / 2.0
    out_prog = inst.program if inst else 0
    out_name = inst.name if inst and inst.name else "Piano"
    out_inst = pretty_midi.Instrument(program=out_prog, name=out_name)

    notes = [n for n in inst.notes if n.end > n.start] if inst else []

    if len(notes) >= 6:
        # Temos notas detectadas no áudio: quantizar o ataque e preservar notas diatônicas/solos
        for n in notes:
            iv = _find_active_interval(intervals, n.start)
            allowed = iv["notes"]
            scale = iv.get("scale", {0, 2, 4, 5, 7, 9, 11})
            pc = n.pitch % 12

            # PRESERVAÇÃO TOTAL DE SOLOS E MELODIAS:
            # Se a nota pertence à escala da música ou ao acorde ativo, mantém a altura original 100%!
            if pc in allowed or pc in scale:
                pitch = n.pitch
            else:
                closest_pc = min(allowed, key=lambda p: min(abs(p - pc), 12 - abs(p - pc)))
                octave = (n.pitch // 12) * 12
                cands = [closest_pc + octave - 12, closest_pc + octave, closest_pc + octave + 12]
                pitch = min([c for c in cands if 0 <= c <= 127], key=lambda c: abs(c - n.pitch))

            pitch = int(np.clip(pitch, 21, 108))

            # Snap ESTRITO para o grid métrico do BPM (16avos / colcheias)
            snap_8th = round(n.start / half_step) * half_step
            snap_16th = round(n.start / step_16th) * step_16th
            if abs(n.start - snap_8th) <= step_16th * 0.70:
                start = max(0.0, snap_8th)
            else:
                start = max(0.0, snap_16th)

            # Duração de semínima/colcheia marcada alinhada à grade (ênfase no tempo)
            raw_dur = n.end - n.start
            dur_steps = max(1, min(4, round(raw_dur / step_16th)))
            dur = max(step_16th, dur_steps * step_16th)
            end = start + dur

            # Ênfase dinâmica no tempo (tempos fortes com mais peso)
            is_downbeat = abs((start / step) % 2) < 0.15
            vel = int(np.clip(n.velocity * 1.1 if is_downbeat else n.velocity, 72, 98))

            out_inst.notes.append(
                pretty_midi.Note(velocity=vel, pitch=int(pitch), start=float(start), end=float(end))
            )

        # Remove duplicatas e limita polifonia
        out_inst.notes.sort(key=lambda x: (x.start, x.pitch))
        cleaned = []
        for note in out_inst.notes:
            if cleaned and abs(cleaned[-1].start - note.start) < 0.02 and cleaned[-1].pitch == note.pitch:
                continue
            cleaned.append(note)
        out_inst.notes = cleaned

    else:
        # Poucas notas detectadas: gerar marcação rítmica simples sobre as cifras
        max_dur = max(duration, max((iv["end"] for iv in intervals), default=30.0))
        t = 0.0
        while t < max_dur:
            iv = _find_active_interval(intervals, t)
            voicing = _chord_voicing(iv["notes"], low_midi=60, high_midi=76)
            beat_index = int(round(t / step))
            is_strong = (beat_index % 2 == 0)
            vel = 88 if is_strong else 76
            dur = step * 0.82

            for pitch in voicing:
                out_inst.notes.append(
                    pretty_midi.Note(
                        velocity=vel,
                        pitch=pitch,
                        start=round(t, 4),
                        end=round(min(max_dur, t + dur), 4),
                    )
                )
            t += step

    return out_inst


def enhance_other_pad(
    inst: pretty_midi.Instrument,
    intervals: list[dict],
    bpm: float = 120.0,
    duration: float = 0.0,
) -> pretty_midi.Instrument:
    """Transforma a faixa 'other' em cama harmônica estável (pad/sustentação afinada)."""
    step = 60.0 / float(bpm)
    step_16th = step / 4.0
    out_prog = inst.program if inst else 0
    out_name = inst.name if inst and inst.name else "Other - Acompanhamento nao identificado"
    out_inst = pretty_midi.Instrument(program=out_prog, name=out_name)

    notes = [n for n in inst.notes if n.end > n.start] if inst else []

    if len(notes) >= 6:
        # Ajusta notas existentes para sustentação consonante, preservando notas diatônicas
        sorted_notes = sorted(notes, key=lambda n: n.start)
        for i, n in enumerate(sorted_notes):
            iv = _find_active_interval(intervals, n.start)
            allowed = iv["notes"]
            scale = iv.get("scale", {0, 2, 4, 5, 7, 9, 11})
            pc = n.pitch % 12

            # PRESERVAÇÃO TOTAL DE ARRANJOS E MELODIAS:
            if pc in allowed or pc in scale:
                pitch = n.pitch
            else:
                closest_pc = min(allowed, key=lambda p: min(abs(p - pc), 12 - abs(p - pc)))
                octave = (n.pitch // 12) * 12
                cands = [closest_pc + octave - 12, closest_pc + octave, closest_pc + octave + 12]
                pitch = min([c for c in cands if 0 <= c <= 127], key=lambda c: abs(c - n.pitch))

            pitch = int(np.clip(pitch, 21, 108))

            # Snap estrito para a grade de 16avos do BPM
            start = max(0.0, round(n.start / step_16th) * step_16th)
            raw_end = max(start + step_16th, round(n.end / step_16th) * step_16th)
            if i + 1 < len(sorted_notes):
                nxt_start = max(0.0, round(sorted_notes[i + 1].start / step_16th) * step_16th)
                if start < nxt_start <= start + step * 4:
                    end = max(raw_end, nxt_start)
                else:
                    end = max(raw_end, start + step)
            else:
                end = max(raw_end, start + step * 2)

            end = max(start + step_16th, round(end / step_16th) * step_16th)
            vel = int(np.clip(n.velocity * 0.85, 48, 70))
            if end - start >= step_16th:
                out_inst.notes.append(
                    pretty_midi.Note(velocity=vel, pitch=int(pitch), start=float(start), end=float(end))
                )
    else:
        # Cama harmônica contínua dos acordes da cifra
        for iv in intervals:
            voicing = _chord_voicing(iv["notes"], low_midi=48, high_midi=68)
            t_start = iv["start"]
            t_end = iv["end"]
            if t_end - t_start < 0.2:
                continue
            dur = max(0.5, t_end - t_start - 0.05)
            for pitch in voicing:
                out_inst.notes.append(
                    pretty_midi.Note(
                        velocity=64,
                        pitch=pitch,
                        start=round(t_start, 4),
                        end=round(t_start + dur, 4),
                    )
                )

    out_inst.notes.sort(key=lambda x: (x.start, x.pitch))
    return out_inst


def enhance_keyboards(
    midi_dir: str | Path,
    bpm: float = 120.0,
    chords: Sequence[tuple[float, str]] = (),
    duration: float = 0.0,
    key: str | None = None,
) -> dict:
    """Aprimora os dois pianos (piano.mid e other.mid) e atualiza completo.mid e instrumental.mid."""
    midi_dir = Path(midi_dir)
    intervals = _get_chord_intervals(chords, duration, default_key=key)

    piano_path = midi_dir / "piano.mid"
    other_path = midi_dir / "other.mid"
    combined_path = midi_dir / "completo.mid"
    instrumental_path = midi_dir / "instrumental.mid"

    piano_midi = pretty_midi.PrettyMIDI(str(piano_path)) if piano_path.is_file() else None
    other_midi = pretty_midi.PrettyMIDI(str(other_path)) if other_path.is_file() else None

    piano_inst = piano_midi.instruments[0] if piano_midi and piano_midi.instruments else None
    other_inst = other_midi.instruments[0] if other_midi and other_midi.instruments else None

    # Se piano não existe como arquivo separado mas há cifras, cria o piano de marcação
    enhanced_piano_inst = enhance_piano_marking(piano_inst, intervals, bpm=bpm, duration=duration)
    enhanced_other_inst = enhance_other_pad(other_inst, intervals, bpm=bpm, duration=duration)

    # Salva piano.mid aprimorado
    new_piano_midi = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
    new_piano_midi.instruments.append(enhanced_piano_inst)
    new_piano_midi.write(str(piano_path))

    # Salva other.mid aprimorado
    new_other_midi = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
    new_other_midi.instruments.append(enhanced_other_inst)
    new_other_midi.write(str(other_path))

    # Atualiza completo.mid
    if combined_path.is_file():
        comb = pretty_midi.PrettyMIDI(str(combined_path))
        # Remove faixas antigas de piano e other
        comb.instruments = [
            i for i in comb.instruments
            if i.name != "Piano" and "other" not in i.name.casefold() and "acompanhamento" not in i.name.casefold()
        ]
        comb.instruments.append(enhanced_piano_inst)
        comb.instruments.append(enhanced_other_inst)
        comb.write(str(combined_path))

        # Atualiza instrumental.mid
        if instrumental_path.parent.exists():
            make_instrumental(combined_path, instrumental_path)

    return {
        "piano_notes": len(enhanced_piano_inst.notes),
        "other_notes": len(enhanced_other_inst.notes),
        "intervals": len(intervals),
        "status": "pianos_harmonized_and_marked",
    }
