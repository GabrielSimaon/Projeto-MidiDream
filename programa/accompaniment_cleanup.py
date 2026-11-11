"""Remove fragmentos fracos do acompanhamento e corrige alturas com contexto.

Trabalha nos eventos originais: não recria andamento, letra, controles ou canais.
Uma nota da voz pode apoiar uma correção, mas não define sozinha a tonalidade.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
import math
from pathlib import Path
import re

import mido
import numpy as np

from midi_timing import TempoMap
from music_guide import ROOTS, chord_notes
from lyrics import _word_tokens, _interpolate_manual_words

MAJOR = (0, 2, 4, 5, 7, 9, 11)
MINOR = (0, 2, 3, 5, 7, 8, 10)
LEVELS = {
    "gentle": (0.28, 0.035),
    "balanced": (0.35, 0.045),
    "strict": (0.45, 0.060),
    # "chords": filtro forte + travamento nas cifras (chord_lock_notes), abaixo.
    "chords": (0.45, 0.060),
}
MAX_VOICES = 8  # Permite acordes completos de violão (6 cordas) e piano sem cortar vozes.
MAX_BEATS = 6  # Nenhuma nota de acompanhamento dura mais que isso (sem sustentos de 30 s).
LOW_LIMIT = 36  # Dó2: preserva bordões de violão (E2=40) e graves naturais do piano.


@dataclass
class Note:
    pitch: int
    velocity: int
    start: float
    end: float
    channel: int
    on_index: int
    off_index: int
    program: int


def _target(name, program, channel):
    name = name.casefold()
    if channel == 9 or any(
        x in name for x in ("vocal", "voice", "choir", "flute", "voz", "bass", "baixo", "cifras", "accordion", "sanfona")
    ):
        return False
    return 0 <= program <= 31 or any(
        x in name for x in ("other", "piano", "teclado", "guitar", "viol", "acompanh")
    )


def read_notes(midi):
    """Liga Note On/Off por canal e altura; suporta notas sobrepostas em FIFO."""
    clock = TempoMap(midi)
    tracks = []
    for track in midi.tracks:
        programs = defaultdict(int)
        opened = defaultdict(deque)
        notes = []
        timeline = []
        tick = 0
        name = next((msg.name for msg in track if msg.type == "track_name"), "")
        for index, msg in enumerate(track):
            tick += msg.time
            timeline.append((tick, msg))
            if msg.type == "program_change":
                programs[msg.channel] = msg.program
            elif msg.type == "note_on" and msg.velocity:
                opened[(msg.channel, msg.note)].append(
                    (index, tick, msg.velocity, programs[msg.channel])
                )
            elif msg.type == "note_off" or (msg.type == "note_on" and not msg.velocity):
                queue = opened[(msg.channel, msg.note)]
                if queue:
                    on, start, velocity, program = queue.popleft()
                    notes.append(
                        Note(
                            msg.note,
                            velocity,
                            clock.ticks_to_seconds(start),
                            clock.ticks_to_seconds(tick),
                            msg.channel,
                            on,
                            index,
                            program,
                        )
                    )
        tracks.append((name, timeline, sorted(notes, key=lambda note: (note.start, note.pitch))))
    return tracks


def infer_scale(notes):
    """Exige cobertura e vantagem entre escalas; não escolhe um tom em caso de empate."""
    weights = np.zeros(12)
    for note in notes:
        weights[note.pitch % 12] += (note.velocity / 127) ** 2 * math.sqrt(
            min(max(note.end - note.start, 0), 0.6)
        )
    if np.count_nonzero(weights) < 5 or not weights.sum():
        return set(), {"source": "insufficient", "coverage": 0.0}
    candidates = []
    for root in range(12):
        scale = {(root + degree) % 12 for degree in MAJOR}
        candidates.append((float(weights[list(scale)].sum() / weights.sum()), root, scale))
    candidates.sort(key=lambda item: item[0], reverse=True)
    best, second = candidates[:2]
    margin = best[0] - second[0]
    report = {
        "source": "automatic",
        "coverage": round(best[0], 4),
        "margin": round(margin, 4),
        "ambiguous": margin < 0.02,
    }
    if best[0] < 0.86 or margin < 0.02:
        return set(), report
    report["major_root"] = int(best[1])
    return best[2], report


def key_scale(key):
    if not key or key == "auto":
        return None
    key_str = str(key).strip()
    match = re.fullmatch(r"([A-G])([#b]?) (maior|menor)", key_str, re.IGNORECASE)
    if match:
        root, accidental, mode = match.groups()
        tonic = (ROOTS[root.upper()] + {"": 0, "#": 1, "b": -1}[accidental]) % 12
        return {(tonic + degree) % 12 for degree in (MAJOR if mode.lower() == "maior" else MINOR)}
    match_short = re.fullmatch(r"([A-G])([#b]?)(m|min|minor|maj|major)?", key_str, re.IGNORECASE)
    if match_short:
        root, accidental, mode = match_short.groups()
        is_minor = bool(mode and mode.lower() in ("m", "min", "minor"))
        tonic = (ROOTS[root.upper()] + {"": 0, "#": 1, "b": -1}[accidental]) % 12
        return {(tonic + degree) % 12 for degree in (MINOR if is_minor else MAJOR)}
    raise ValueError("Tonalidade inválida. Use, por exemplo, C maior ou A menor.")


def timed_chords(text, marks, detected_words, duration, bpm=120.0):
    """Relaciona cifras às palavras alinhadas ou à linha do tempo da música.
    
    Garante que os acordes avancem no tempo compasso a compasso, impedindo que múltiplos
    acordes colapsem no mesmo segundo e façam o piano tocar apenas uma nota durante a música toda.
    """
    aligned = _interpolate_manual_words(
        [{"text": token} for token in _word_tokens(text)], detected_words, duration
    ) if detected_words else []

    if not aligned:
        events = []
        has_explicit = any(m.get("seconds") is not None for m in marks)
        if has_explicit:
            for mark in marks:
                s = mark.get("seconds")
                if s is not None and 0 <= s < duration:
                    events.append((float(s), mark["symbol"]))
        elif marks and duration > 0:
            step = duration / len(marks)
            events = [(i * step, m["symbol"]) for i, m in enumerate(marks)]
        return sorted(events)

    t_first_word = float(aligned[0]["start"]) if aligned else 0.0
    step_beat = 60.0 / max(30.0, float(bpm))
    min_chord_step = step_beat * 1.5

    marks_with_time = []
    intro_marks = []
    lyric_marks = []

    for mark in marks:
        if mark.get("seconds") is not None:
            marks_with_time.append((float(mark["seconds"]), mark["symbol"]))
            continue
        word_idx = len(_word_tokens(text[: mark["offset"]]))
        if word_idx == 0 and len(marks) > 1 and len(aligned) > 1:
            intro_marks.append(mark["symbol"])
        else:
            w_time = aligned[min(word_idx, len(aligned) - 1)]["start"]
            lyric_marks.append((float(w_time), mark["symbol"]))

    if intro_marks:
        n_intro = len(intro_marks)
        if n_intro == 1 and not lyric_marks:
            marks_with_time.append((round(t_first_word, 3), intro_marks[0]))
        elif n_intro == 1 and lyric_marks:
            # Acorde isolado antes da letra começa no início se a introdução for longa
            start_t = 0.0 if t_first_word >= 2.0 else t_first_word
            marks_with_time.append((round(start_t, 3), intro_marks[0]))
        elif t_first_word >= n_intro * step_beat * 1.0:
            step_intro = t_first_word / n_intro
            for i, sym in enumerate(intro_marks):
                marks_with_time.append((round(i * step_intro, 3), sym))
        else:
            bar_dur = step_beat * 4.0
            for i, sym in enumerate(intro_marks):
                marks_with_time.append((round(i * bar_dur, 3), sym))

    marks_with_time.sort(key=lambda m: m[0])
    last_time = marks_with_time[-1][0] + min_chord_step if marks_with_time else 0.0

    for w_time, sym in lyric_marks:
        actual_time = max(w_time, last_time)
        if actual_time < duration:
            marks_with_time.append((round(actual_time, 3), sym))
            last_time = actual_time + min_chord_step

    marks_with_time.sort(key=lambda m: m[0])
    deduped = []
    for t, sym in marks_with_time:
        if 0 <= t < duration:
            if deduped and abs(deduped[-1][0] - t) < 0.10:
                deduped[-1] = (t, sym)
            else:
                deduped.append((t, sym))

    return deduped


def filter_midi(
    source,
    destination,
    *,
    level="balanced",
    correct_pitch=True,
    key=None,
    chords=(),
    vocal_notes=(),
    scale_hint=None,
):
    """Filtra piano/Other/guitarra. Voz, baixo e bateria não são alterados.

    A correção é limitada a um semitom em notas fracas e precisa de uma escolha
    harmônica inequívoca. Não transpõe a música inteira nem força todas as notas.
    """
    if level not in (*LEVELS, "off"):
        raise ValueError("Filtro inválido: use off, gentle, balanced, strict ou chords.")
    manual_scale = key_scale(key)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(source, charset="latin1")
    tracks = read_notes(midi)
    targets = [
        note
        for name, _, notes in tracks
        for note in notes
        if _target(name, note.program, note.channel)
    ]
    automatic, scale_report = infer_scale(targets)
    scale = (
        manual_scale
        if manual_scale is not None
        else (set(scale_hint) if scale_hint is not None else automatic)
    )
    report = {
        "level": level,
        "before": len(targets),
        "after": len(targets),
        "removed": 0,
        "corrected": 0,
        "scale": sorted(scale),
        "key_analysis": scale_report,
        "manual_key": key or "",
        "changes": [],
    }
    if level == "off":
        midi.save(destination)
        return report
    relative, minimum = LEVELS[level]
    tempo = next(
        (msg.tempo for msg in mido.merge_tracks(midi.tracks) if msg.type == "set_tempo"), 500000
    )
    beat = tempo / 1e6
    window = max(0.5, min(2.0, beat * 2))
    chord_times = [event[0] for event in chords]
    for track_index, (name, timeline, all_notes) in enumerate(tracks):
        notes = [note for note in all_notes if _target(name, note.program, note.channel)]
        if not notes:
            continue
        starts = [note.start for note in notes]
        decisions = {}
        weak_flags = {}
        for note in notes:
            local = notes[
                bisect_left(starts, note.start - window) : bisect_right(starts, note.start + window)
            ]
            reference = float(np.percentile([other.velocity for other in local], 85))
            weak = note.velocity < max(8.0, reference * relative) and note.velocity < 28
            weak_flags[note.on_index] = weak
            duration = note.end - note.start
            # Remove apenas ruídos fantasmas espúrios reais (muito curtos e fracos)
            if weak and duration < minimum and duration < 0.040:
                decisions[note.on_index] = (None, "weak_fragment")
                continue
            active_chord = set()
            index = bisect_right(chord_times, note.start) - 1
            if index >= 0:
                active_chord = {pitch % 12 for pitch in chord_notes(chords[index][1])}
            sung = {
                v.pitch % 12
                for v in vocal_notes
                if v.start <= note.start < v.end and v.end - v.start >= 0.1
            }
            allowed = scale | active_chord | sung
            pitch = note.pitch
            # Fragmentos dissonantes só são removidos se o tom foi explicitado e a nota for ruído fraco
            if (
                correct_pitch and key and allowed and pitch % 12 not in allowed
                and duration < min(0.12, beat * 0.25)
                and note.velocity < 25
            ):
                decisions[note.on_index] = (None, "dissonant_fragment")
                continue
            if (
                weak
                and correct_pitch
                and key
                and (scale or active_chord)
                and allowed
                and pitch % 12 not in allowed
            ):
                choices = [
                    candidate
                    for candidate in (pitch - 1, pitch + 1)
                    if 0 <= candidate <= 127 and candidate % 12 in allowed
                ]
                preferred = [candidate for candidate in choices if candidate % 12 in active_chord]
                if len(preferred) == 1:
                    choices = preferred
                else:
                    supported = [
                        candidate
                        for candidate in choices
                        if any(
                            other.pitch == candidate
                            and other.velocity >= reference * 0.75
                            and abs(other.start - note.start) <= beat
                            for other in local
                        )
                    ]
                    if len(supported) == 1:
                        choices = supported
                if len(choices) == 1:
                    pitch = choices[0]
                elif duration < min(0.25, beat * 0.5) and note.velocity < reference * 0.25:
                    decisions[note.on_index] = (None, "weak_dissonance")
                    continue
            decisions[note.on_index] = (
                pitch,
                "harmonic_correction" if pitch != note.pitch else "keep",
            )
        # Corrigir uma nota não pode criar uma duplicata sobre uma nota principal.
        accepted = defaultdict(list)
        for note in sorted(notes, key=lambda item: (-item.velocity, item.start)):
            pitch, reason = decisions[note.on_index]
            if pitch is None:
                continue
            overlap = any(
                other.channel == note.channel
                and target == pitch
                and min(other.end, note.end) - max(other.start, note.start)
                >= max(0.025, (note.end - note.start) * 0.75)
                for other, target in accepted[(note.channel, pitch)]
            )
            if overlap and weak_flags[note.on_index]:
                decisions[note.on_index] = (None, "weak_duplicate")
            else:
                accepted[(note.channel, pitch)].append((note, pitch))
        changes = {}
        removed = set()
        for note in notes:
            pitch, reason = decisions[note.on_index]
            if pitch is None:
                removed.update((note.on_index, note.off_index))
                report["removed"] += 1
            elif pitch != note.pitch:
                changes[note.on_index] = pitch
                changes[note.off_index] = pitch
                report["corrected"] += 1
            if reason != "keep":
                report["changes"].append(
                    {
                        "track": name,
                        "seconds": round(note.start, 6),
                        "old_pitch": int(note.pitch),
                        "new_pitch": int(pitch) if pitch is not None else None,
                        "reason": reason,
                    }
                )
        # Recalcula apenas os deltas após retirar eventos. Tempos absolutos ficam iguais.
        track = mido.MidiTrack()
        previous = 0
        for index, (tick, msg) in enumerate(timeline):
            if index in removed:
                continue
            replacement = msg.copy(note=changes[index]) if index in changes else msg.copy()
            track.append(replacement.copy(time=tick - previous))
            previous = tick
        midi.tracks[track_index] = track
    report["after"] = report["before"] - report["removed"]
    if level == "chords" and chords:
        report["chord_lock"] = chord_lock_midi(midi, chords, scale, beat)
        report["after"] = report["chord_lock"]["after"]
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    midi.save(destination)
    return report


def _chord_at(chords, times, seconds):
    index = bisect_right(times, seconds) - 1
    return index if index >= 0 else None


def chord_lock_notes(notes, chords, scale, beat):
    """Prende as notas de acompanhamento às cifras do usuário e desfaz o "empastelamento".

    1. Nota fora do acorde: vira a nota do acorde mais próxima (até 2 semitons); sem
       candidata, só sobrevive se for forte, longa e da escala; senão é removida.
    2. Nota que atravessa a troca de acorde sem pertencer ao novo acorde é cortada na troca.
    3. Notas graves (< Dó3) sobem de oitava; a faixa do baixo cuida dessa região.
    4. Mesma altura repetida/sobreposta vira uma só nota.
    5. Nenhuma nota passa de MAX_BEATS tempos.
    6. No máximo MAX_VOICES notas ao mesmo tempo; sobrevivem as mais fortes.
    Retorna (notas novas, contagem por motivo). Cada nota é um objeto Note.
    """
    times = [c[0] for c in chords]
    tones = []
    for _, symbol in chords:
        try:
            tones.append({n % 12 for n in chord_notes(symbol)})
        except ValueError:
            tones.append(set())
    stats = defaultdict(int)
    if not notes:
        return [], stats
    ref = float(np.percentile([n.velocity for n in notes], 85))
    kept = []
    for note in sorted(notes, key=lambda n: (n.start, n.pitch)):
        # Antecipações de até 60 ms já pertencem ao acorde seguinte.
        index = _chord_at(chords, times, note.start + 0.06)
        pitch, end = note.pitch, note.end
        if index is not None and tones[index]:
            allowed = tones[index]
            if pitch % 12 not in allowed:
                if end - note.start < min(0.10, beat * 0.25) and note.velocity < ref * 0.75:
                    stats["dissonant_fragment"] += 1
                    continue
                strong = (
                    note.velocity >= ref * 0.8
                    and end - note.start >= beat * 0.5
                    and pitch % 12 in scale
                )
                options = sorted(
                    (c for c in (pitch - 1, pitch + 1, pitch - 2, pitch + 2) if c % 12 in allowed),
                    key=lambda c: (abs(c - pitch), c > pitch),
                )
                if strong:
                    stats["scale_kept"] += 1
                elif options and 0 <= options[0] <= 127:
                    pitch = options[0]
                    stats["snapped"] += 1
                else:
                    stats["dropped_off_chord"] += 1
                    continue
            nxt = index + 1
            if nxt < len(chords):
                change = chords[nxt][0]
                if (
                    note.start < change - 0.08
                    and end > change + 0.08
                    and pitch % 12 not in tones[nxt]
                ):
                    end = change
                    stats["cut_at_change"] += 1
        if end - note.start > beat * MAX_BEATS:
            end = note.start + beat * MAX_BEATS
            stats["capped_length"] += 1
        while pitch < LOW_LIMIT:
            pitch += 12
            stats["octave_up"] += 1
        if end - note.start < 0.04:
            stats["too_short"] += 1
            continue
        kept.append(Note(pitch, note.velocity, note.start, end, note.channel, 0, 0, note.program))
    # Funde a mesma altura que se repete ou se sobrepõe.
    merged = []
    last = {}
    for note in sorted(kept, key=lambda n: (n.pitch, n.start)):
        previous = last.get(note.pitch)
        if previous is not None and note.start <= previous.end + 0.04:
            previous.end = max(previous.end, note.end)
            previous.velocity = max(previous.velocity, note.velocity)
            stats["merged"] += 1
        else:
            last[note.pitch] = note
            merged.append(note)
    # Limita a polifonia: a nota mais fraca cede (a entrante é descartada ou a ativa é cortada).
    result = []
    active = []
    for note in sorted(merged, key=lambda n: (n.start, -n.velocity)):
        active = [a for a in active if a.end > note.start + 0.005]
        if len(active) >= MAX_VOICES:
            weakest = min(active, key=lambda a: a.velocity)
            if weakest.velocity >= note.velocity:
                stats["voice_limit"] += 1
                continue
            weakest.end = note.start
            stats["voice_limit"] += 1
            active.remove(weakest)
        active.append(note)
        result.append(note)
    result = [n for n in result if n.end - n.start >= 0.03]
    return sorted(result, key=lambda n: (n.start, n.pitch)), dict(stats)


def chord_lock_midi(midi, chords, scale, beat):
    """Reescreve as faixas de acompanhamento do objeto MidiFile usando as cifras."""
    clock = TempoMap(midi)
    summary = defaultdict(int)
    before = after = 0
    for index, (name, timeline, all_notes) in enumerate(read_notes(midi)):
        notes = [n for n in all_notes if _target(name, n.program, n.channel)]
        if not notes:
            continue
        locked, stats = chord_lock_notes(notes, chords, scale, beat)
        before += len(notes)
        after += len(locked)
        for key, value in stats.items():
            summary[key] += value
        # Mantém tudo que não é nota (nome, programa, volume) nos mesmos tempos absolutos.
        events = []
        for tick, msg in timeline:
            if msg.type not in ("note_on", "note_off") and msg.type != "end_of_track":
                events.append((tick, 1, msg))
        end_tick = max((t for t, _ in timeline), default=0)
        for n in locked:
            start = clock.seconds_to_ticks(n.start)
            stop = max(start + 1, clock.seconds_to_ticks(n.end))
            end_tick = max(end_tick, stop)
            events.append((start, 2, mido.Message("note_on", channel=n.channel, note=n.pitch, velocity=max(1, min(127, n.velocity)), time=0)))
            events.append((stop, 0, mido.Message("note_off", channel=n.channel, note=n.pitch, velocity=0, time=0)))
        events.sort(key=lambda item: (item[0], item[1]))
        track = mido.MidiTrack()
        previous = 0
        for tick, _, msg in events:
            track.append(msg.copy(time=tick - previous))
            previous = tick
        track.append(mido.MetaMessage("end_of_track", time=max(0, end_tick - previous)))
        midi.tracks[index] = track
    return {"before": before, "after": after, **dict(summary)}


def filter_result_midis(
    midi_dir,
    result_dir,
    *,
    text,
    marks,
    detected_words,
    duration,
    level="balanced",
    correct_pitch=True,
    key=None,
):
    """Aplica o mesmo contexto às faixas individuais e ao combinado antes do KAR."""
    import json
    import shutil

    midi_dir = Path(midi_dir)
    combined = midi_dir / "completo.mid"
    tracks = read_notes(mido.MidiFile(combined, charset="latin1"))
    targets = [
        note
        for name, _, notes in tracks
        for note in notes
        if _target(name, note.program, note.channel)
    ]
    scale, analysis = infer_scale(targets)
    vocals = [
        note
        for name, _, notes in tracks
        for note in notes
        if note.channel != 9
        and (
            any(label in name.casefold() for label in ("vocal", "voice", "choir", "flute", "voz"))
            or note.program in (52, 53, 73, 74)
        )
    ]
    events = timed_chords(text, marks, detected_words, duration)
    reports = {}
    backup = Path(result_dir) / "midi_antes_filtro"
    if level != "off":
        backup.mkdir(exist_ok=True)
        for file in midi_dir.glob("*.mid"):
            shutil.copy2(file, backup / file.name)
    for file in sorted(midi_dir.glob("*.mid")):
        reports[file.name] = filter_midi(
            file,
            file,
            level=level,
            correct_pitch=correct_pitch,
            key=key,
            chords=events,
            vocal_notes=vocals,
            scale_hint=scale,
        )
    output = {"key_analysis": analysis, "timed_chords": len(events), "files": reports}
    path = Path(result_dir) / "relatorio_filtro_acompanhamento.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
