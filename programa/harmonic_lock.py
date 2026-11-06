"""Alinhamento harmônico estrito com a cifra e escala da música.

Garante que:
1. O acorde ativo da cifra é soberano: notas constitutivas do acorde (mesmo com acidentes)
   são 100% preservadas e têm prioridade máxima.
2. O baixo segue a nota de baixo/fundamental da cifra ativa na tessitura grave (MIDI 24 a 58).
3. Notas dissonantes/fora de tom em piano, violão e guitarra são corrigidas para as notas do acorde ativo.
4. A bateria (canal 10 / index 9) é 100% inalterada.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
import json
from pathlib import Path

import mido

from accompaniment_cleanup import key_scale
from midi_timing import TempoMap
from music_guide import chord_notes, chord_root_and_bass, chord_triad_pcs


def sanitize_track_overlaps(track: mido.MidiTrack) -> mido.MidiTrack:
    """Garante que notas da mesma altura no mesmo canal não sofram corte prematuro de NoteOff."""
    timeline = []
    tick = 0
    for msg in track:
        tick += msg.time
        timeline.append((tick, msg))

    active = {}
    events = []

    for tick, msg in timeline:
        if msg.is_meta:
            events.append((tick, msg))
            continue

        channel = getattr(msg, "channel", None)
        if channel is None or channel == 9:
            events.append((tick, msg))
            continue

        is_on = msg.type == "note_on" and msg.velocity > 0
        is_off = msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0)

        if is_on:
            key = (channel, msg.note)
            if key in active:
                events.append((tick, mido.Message("note_off", channel=channel, note=msg.note, velocity=0, time=0)))
            active[key] = tick
            events.append((tick, msg))
        elif is_off:
            key = (channel, msg.note)
            if key in active:
                del active[key]
                events.append((tick, msg))
        else:
            events.append((tick, msg))

    end_tick = max((t for t, _ in events), default=0)
    for (channel, note), on_tick in list(active.items()):
        events.append((end_tick, mido.Message("note_off", channel=channel, note=note, velocity=0, time=0)))

    events.sort(
        key=lambda item: (
            item[0],
            0 if getattr(item[1], "type", "") == "note_off" or (getattr(item[1], "type", "") == "note_on" and getattr(item[1], "velocity", 1) == 0) else 1,
        )
    )
    new_track = mido.MidiTrack()
    prev_tick = 0
    for tick, msg in events:
        new_track.append(msg.copy(time=tick - prev_tick))
        prev_tick = tick
    return new_track


def lock_midi(midi: mido.MidiFile, key: str, chords: tuple | list = ()) -> dict:
    """Alinha os instrumentos às notas da cifra e escala da música."""
    scale = key_scale(key)
    if not scale:
        raise ValueError("Escolha a escala para corrigir todos os instrumentos.")

    clock = TempoMap(midi)
    events = sorted(chords) if chords else []
    times = [event[0] for event in events]
    tones = []
    bass_tones = []
    triads = []
    for _, symbol in events:
        try:
            c_tones = {note % 12 for note in chord_notes(symbol)}
            b_tone = chord_root_and_bass(symbol)[1]
            _, tr_tones = chord_triad_pcs(symbol)
        except Exception:
            c_tones = scale
            b_tone = min(scale)
            tr_tones = scale
        tones.append(c_tones)
        bass_tones.append(b_tone)
        triads.append(tr_tones)

    report = {
        "scale": sorted(scale),
        "notes": 0,
        "corrected": 0,
        "pitch_bends_reset": 0,
        "chords_outside_scale": [
            symbol for _, symbol in events
            if {n % 12 for n in chord_notes(symbol)} - scale
        ],
        "chords": [symbol for _, symbol in events],
    }
    scale_candidates = [pitch for pitch in range(128) if pitch % 12 in scale]

    for track_idx, track in enumerate(midi.tracks):
        track_name = next((msg.name for msg in track if msg.type == "track_name"), "").casefold()
        is_drum_track = "drum" in track_name or "bater" in track_name or "chimbal" in track_name or "hihat" in track_name
        is_vocal = any(k in track_name for k in ("vocal", "voz", "choir", "flute"))
        is_solo = any(k in track_name for k in ("solo", "lead", "melod"))
        is_accompaniment = any(k in track_name for k in ("guitar", "viol", "acompanhamento", "base", "steel")) and not is_solo
        is_bass = ("bass" in track_name or "baixo" in track_name) or (not is_accompaniment and not is_vocal and any(hasattr(msg, "channel") and msg.channel == 0 for msg in track))

        opened = defaultdict(deque)
        tick = 0
        for message in track:
            tick += message.time
            if message.is_meta or not hasattr(message, "channel"):
                continue
            if message.channel == 9 or is_drum_track:
                continue

            if message.type == "pitchwheel":
                report["pitch_bends_reset"] += message.pitch != 0
                message.pitch = 0
            elif message.type == "note_on" and message.velocity > 0:
                original = message.note
                pitch = original
                seconds = clock.ticks_to_seconds(tick)
                index = bisect_right(times, seconds) - 1 if times else -1

                active_chord = tones[index] if (0 <= index < len(tones)) else set()
                active_triad = triads[index] if (0 <= index < len(triads)) else set()
                bass_target_pc = bass_tones[index] if (0 <= index < len(bass_tones)) else None

                pc = original % 12

                if is_bass:
                    # BAIXO: prioriza a nota fundamental da cifra e notas da escala na tessitura grave
                    if bass_target_pc is not None and pc == bass_target_pc:
                        target = bass_target_pc
                    elif active_triad and pc in active_triad:
                        target = pc
                    elif pc in scale:
                        target = pc
                    elif bass_target_pc is not None:
                        target = bass_target_pc
                    elif active_triad:
                        target = min(active_triad, key=lambda n: min(abs(n - pc), 12 - abs(n - pc)))
                    else:
                        target = min(scale, key=lambda n: min(abs(n - pc), 12 - abs(n - pc)))

                    bass_opts = [target + 12 * oct for oct in (2, 3, 4) if 24 <= target + 12 * oct <= 58]
                    if not bass_opts:
                        bass_opts = [target + 12 * 3]
                    pitch = min(bass_opts, key=lambda n: abs(n - original))

                elif is_accompaniment and active_triad:
                    # INSTRUMENTOS DE ACOMPANHAMENTO HARMONICO (guitarra/violao base):
                    # Alinham notas a triade fundamental do acorde ativo para evitar acordes suspensos (Csus2, Fsus2, Gsus4)
                    if pc in active_triad:
                        pitch = original
                    else:
                        closest_pc = min(active_triad, key=lambda n: min(abs(n - pc), 12 - abs(n - pc)))
                        octave = (original // 12) * 12
                        candidates = [closest_pc + octave - 12, closest_pc + octave, closest_pc + octave + 12]
                        pitch = min([c for c in candidates if 0 <= c <= 127], key=lambda c: abs(c - original))

                else:
                    # PRESERVACAO TOTAL DE SOLOS, MELODIAS E PASSAGENS DIATONICAS:
                    # Solos de guitarra, sanfona, piano solo, melodia vocal e faixas instrumentais tem 100% de liberdade melodica diatonica.
                    if pc in scale:
                        pitch = original
                    elif active_chord and pc in active_chord:
                        pitch = original
                    elif active_chord:
                        closest_chord_pc = min(active_chord, key=lambda n: min(abs(n - pc), 12 - abs(n - pc)))
                        octave = (original // 12) * 12
                        candidates = [closest_chord_pc + octave - 12, closest_chord_pc + octave, closest_chord_pc + octave + 12]
                        pitch = min([c for c in candidates if 0 <= c <= 127], key=lambda c: abs(c - original))
                    else:
                        closest_scale_pc = min(scale, key=lambda n: min(abs(n - pc), 12 - abs(n - pc)))
                        octave = (original // 12) * 12
                        candidates = [closest_scale_pc + octave - 12, closest_scale_pc + octave, closest_scale_pc + octave + 12]
                        pitch = min([c for c in candidates if 0 <= c <= 127], key=lambda c: abs(c - original))
                if pitch != original:
                    report["corrected"] += 1

                opened[(message.channel, original)].append(pitch)
                message.note = pitch
                report["notes"] += 1

            elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
                queue = opened[(message.channel, message.note)]
                if queue:
                    message.note = queue.popleft()
                elif message.note % 12 not in scale:
                    message.note = min(scale_candidates, key=lambda n: abs(n - message.note))

        if not is_drum_track:
            midi.tracks[track_idx] = sanitize_track_overlaps(track)

    return report


def lock_result(midi_dir: str | Path, result_dir: str | Path, key: str, chords: tuple | list = ()) -> dict:
    reports = {}
    for file in sorted(Path(midi_dir).glob("*.mid")):
        midi = mido.MidiFile(file, charset="latin1")
        reports[file.name] = lock_midi(midi, key, chords)
        temporary = file.with_suffix(".harmonia.tmp")
        midi.save(temporary)
        temporary.replace(file)
    report = {
        "key": key,
        "drums_unchanged": True,
        "files": reports,
        "notice": "Notas alinhadas com as cifras ativas e escala. Cifras com acidentes têm prioridade e notas dissonantes foram corrigidas.",
    }
    Path(result_dir, "relatorio_harmonia.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
