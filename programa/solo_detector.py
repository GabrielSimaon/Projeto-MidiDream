"""Detecção e extração dedicada de solos de teclado/piano (PianoSolo).

Atende estritamente à solicitação do usuário:
1. 'O solo é pra ser feito por apenas 1 piano(no estado atual do programa ele é tocado por dois pianos),
    defina apenas 1 teclado pro solo e o outro pra acompanhar'.
2. 'ja o piano do solo tem liberdade pra flutuar em estacato, porém sem tocar notas que n existam'.
3. 'Remove as notas de solo da base de acompanhamento (piano.mid e other.mid)'.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Sequence
import numpy as np
import pretty_midi

from accompaniment_cleanup import key_scale
from music_guide import chord_notes, chord_triad_pcs


def detect_solo_intervals(
    duration: float,
    detected_words: Sequence[dict[str, Any]] | None = None,
    guide_text: str | None = None,
    min_middle_gap: float = 2.4,
) -> list[dict[str, Any]]:
    solo_intervals: list[dict[str, Any]] = []
    dur = float(duration) if duration and duration > 0 else 180.0

    valid_words = [
        w for w in (detected_words or [])
        if float(w.get("end", 0)) > float(w.get("start", 0))
        and w.get("text", "").strip() not in ("[QUEBRA]", "")
    ]

    # 1. COMEÇO (Introdução / Solo de Abertura)
    if valid_words:
        first_vocal_start = float(valid_words[0]["start"])
        if first_vocal_start >= 1.5:
            solo_intervals.append({
                "start": 0.0,
                "end": max(0.5, first_vocal_start - 0.15),
                "type": "comeco",
                "label": f"Solo de Introdução (0.0s a {first_vocal_start:.2f}s)",
            })
    else:
        intro_end = min(15.0, dur * 0.15)
        solo_intervals.append({
            "start": 0.0,
            "end": intro_end,
            "type": "comeco",
            "label": f"Solo de Introdução (0.0s a {intro_end:.2f}s)",
        })

    # 2. MEIO (Interlúdios entre estrofes/refrões e pausas vocais instrumentais)
    if valid_words:
        for i in range(len(valid_words) - 1):
            w_curr = valid_words[i]
            w_next = valid_words[i + 1]
            gap_start = float(w_curr["end"])
            gap_end = float(w_next["start"])
            gap_len = gap_end - gap_start

            if gap_len >= min_middle_gap:
                t1 = round(gap_start + 0.10, 2)
                t2 = round(gap_end - 0.10, 2)
                if t2 > t1:
                    w1 = str(w_curr.get("text", "")).strip()
                    w2 = str(w_next.get("text", "")).strip()
                    solo_intervals.append({
                        "start": t1,
                        "end": t2,
                        "type": "meio",
                        "label": f"Solo / Interlúdio Meio ({t1}s a {t2}s | '{w1}' -> '{w2}')",
                    })

    # 3. FINAL (Solo de Encerramento / Outro)
    if valid_words:
        last_vocal_end = float(valid_words[-1]["end"])
        if dur > last_vocal_end + 1.0:
            solo_intervals.append({
                "start": round(last_vocal_end + 0.10, 2),
                "end": round(dur, 2),
                "type": "final",
                "label": f"Solo Final / Encerramento ({last_vocal_end:.2f}s a {dur:.2f}s)",
            })
    else:
        outro_start = max(0.0, dur - 15.0)
        solo_intervals.append({
            "start": outro_start,
            "end": dur,
            "type": "final",
            "label": f"Solo Final / Encerramento ({outro_start:.2f}s a {dur:.2f}s)",
        })

    return solo_intervals


def is_in_solo_window(time_sec: float, solo_intervals: Sequence[dict[str, Any]], margin: float = 0.08) -> bool:
    for iv in solo_intervals:
        if (iv["start"] - margin) <= time_sec <= (iv["end"] + margin):
            return True
    return False


def split_piano_solo(
    piano_midi_path: str | Path,
    other_midi_path: str | Path | None = None,
    drums_midi_path: str | Path | None = None,
    solo_intervals: Sequence[dict[str, Any]] | None = None,
    output_solo_path: str | Path | None = None,
    output_piano_path: str | Path | None = None,
    bpm: float = 120.0,
    solo_program: int = 0,
    key: str | None = None,
    chords: Sequence[tuple[float, str]] = (),
    solo_instrument: str = "piano",
) -> dict[str, Any]:
    piano_p = Path(piano_midi_path)
    if not piano_p.is_file():
        return {"solo_notes": 0, "base_notes": 0, "status": "no_piano_file"}

    solo_norm = str(solo_instrument or "piano").lower().strip()
    if any(g in solo_norm for g in ("electric", "eletrica", "elétrica", "guitarra", "guitar")):
        solo_program = 27
        solo_name = "Electric Guitar Solo"
    elif any(a in solo_norm for a in ("violao", "violão", "acoustic", "acustico", "acústico")):
        solo_program = 25
        solo_name = "Acoustic Guitar Solo"
    else:
        solo_program = solo_program if solo_program != 0 else 0
        solo_name = "Piano Solo"

    out_solo_p = Path(output_solo_path) if output_solo_path else piano_p.with_name("piano_solo.mid")
    out_piano_p = Path(output_piano_path) if output_piano_path else piano_p

    pm_piano = pretty_midi.PrettyMIDI(str(piano_p))
    # Select the piano instrument with most notes as solo, others as accompaniment
    piano_insts = [inst for inst in pm_piano.instruments if not inst.is_drum and inst.program == 0]
    if not piano_insts:
        return {"solo_notes": 0, "base_notes": 0, "status": "no_piano"}
    # Choose solo instrument (most note density)
    solo_inst = max(piano_insts, key=lambda i: len(i.notes))
    # Re‑program other piano instruments to accompaniment program (default 1)
    accompaniment_program = 1
    for inst in piano_insts:
        if inst is not solo_inst:
            inst.program = accompaniment_program
    solo_inst.program = 0  # ensure solo program
    inst_piano = solo_inst
    if not inst_piano.notes:
        return {"solo_notes": 0, "base_notes": 0, "status": "empty_piano"}

    # Carrega também other.mid se existir para capturar possíveis notas de solo vazadas
    other_notes = []
    pm_oth = None
    if other_midi_path:
        oth_p = Path(other_midi_path)
        if oth_p.is_file():
            try:
                pm_oth = pretty_midi.PrettyMIDI(str(oth_p))
                if pm_oth.instruments:
                    other_notes = pm_oth.instruments[0].notes
            except Exception:
                pass

    # Carrega ataques da bateria para rejeitar transientes de percussão ('O piano do solo ta confundindo com a bateria')
    drum_attacks = set()
    if drums_midi_path:
        dp = Path(drums_midi_path)
        if dp.is_file():
            try:
                d_pm = pretty_midi.PrettyMIDI(str(dp))
                for di in d_pm.instruments:
                    if di.is_drum:
                        for dn in di.notes:
                            drum_attacks.add(round(dn.start, 3))
            except Exception:
                pass

    intervals = list(solo_intervals or [])
    if not intervals:
        dur = max((n.end for n in inst_piano.notes), default=60.0)
        intervals = detect_solo_intervals(duration=dur)

    # Identifica a escala e acordes para conferência de notas existentes ('sem tocar notas que n existam')
    scale = None
    if key:
        try:
            scale = key_scale(key)
        except Exception:
            scale = None
    if not scale and chords:
        scale = set()
        for _, sym in chords:
            try:
                scale.update(n % 12 for n in chord_notes(sym))
            except Exception:
                pass
    if not scale:
        scale = {0, 2, 4, 5, 7, 9, 11}

    chord_events = sorted(chords, key=lambda c: c[0]) if chords else []

    def get_chord_pcs(t: float) -> set[int]:
        if not chord_events:
            return scale
        curr = chord_events[0][1]
        for c_t, sym in chord_events:
            if c_t <= t + 0.05:
                curr = sym
            else:
                break
        try:
            return {n % 12 for n in chord_notes(curr)}
        except Exception:
            return scale

    all_piano_notes = sorted(inst_piano.notes, key=lambda n: (n.start, n.pitch))
    raw_solo_candidates: list[pretty_midi.Note] = []
    base_notes: list[pretty_midi.Note] = []

    # Agrupa notas por instante de ataque (janela de 40ms)
    time_clusters: list[list[pretty_midi.Note]] = []
    for n in all_piano_notes:
        if not time_clusters or abs(time_clusters[-1][0].start - n.start) > 0.04:
            time_clusters.append([n])
        else:
            time_clusters[-1].append(n)

    for cluster in time_clusters:
        c_start = cluster[0].start
        in_solo = is_in_solo_window(c_start, intervals)

        if not in_solo:
            # FORA DO SOLO: Todas as notas vão 100% para a base de acompanhamento!
            # PianoSolo permanece estritamente silencioso aqui.
            for n in cluster:
                base_notes.append(n)
        else:
            # DENTRO DO INTERVALO DE SOLO (Começo, Meio ou Final):
            # Identifica a linha melódica principal de solo
            if len(cluster) == 1:
                n = cluster[0]
                if n.pitch >= 55:
                    raw_solo_candidates.append(n)
                else:
                    base_notes.append(n)
            else:
                max_pitch = max(n.pitch for n in cluster)
                for n in cluster:
                    if n.pitch == max_pitch and n.pitch >= 60:
                        raw_solo_candidates.append(n)
                    elif n.pitch < 60:
                        base_notes.append(n)

    # Verifica também em other_notes se há licks de solo nos intervalos que não estavam no piano
    if other_notes:
        for oth_n in other_notes:
            if is_in_solo_window(oth_n.start, intervals) and oth_n.pitch >= 65:
                is_duplicate = any(
                    abs(s.start - oth_n.start) < 0.05 and abs(s.pitch - oth_n.pitch) == 0
                    for s in raw_solo_candidates
                )
                if not is_duplicate:
                    raw_solo_candidates.append(oth_n)

    # FILTRAGEM RIGOROSA DE NOTAS FANTASMAS E LIBERDADE DE STACCATO:
    # 'ja o piano do solo tem liberdade pra flutuar em estacato, porém sem tocar notas que n existam'
    solo_notes: list[pretty_midi.Note] = []
    raw_solo_candidates.sort(key=lambda n: (n.start, n.pitch))

    # Agrupa por tempo de ataque para priorizar linha de voz única (sem embolar com vazamentos)
    solo_clusters: list[list[pretty_midi.Note]] = []
    for n in raw_solo_candidates:
        if not solo_clusters or abs(solo_clusters[-1][0].start - n.start) > 0.035:
            solo_clusters.append([n])
        else:
            solo_clusters[-1].append(n)

    for s_cluster in solo_clusters:
        # Prioriza a nota principal melódica (mais aguda/mais forte)
        lead_note = max(s_cluster, key=lambda n: n.pitch)
        
        # Filtro de notas fantasmas (ruído muito fraco ou espúrio)
        if lead_note.velocity < 48 and lead_note.end - lead_note.start < 0.045:
            continue

        # Rejeita transientes de bateria que sangraram no canal do piano ('O piano do solo ta confundindo com a bateria')
        dur = max(0.045, lead_note.end - lead_note.start)
        if drum_attacks and dur < 0.12 and any(abs(lead_note.start - dt) <= 0.030 for dt in drum_attacks):
            continue

        pc = lead_note.pitch % 12
        allowed_chord = get_chord_pcs(lead_note.start)
        
        # Correção harmônica: 'sem tocar notas que n existam'
        if pc in scale or pc in allowed_chord:
            final_pitch = lead_note.pitch
        else:
            allowed_union = scale | allowed_chord
            closest_pc = min(allowed_union, key=lambda p: min(abs(p - pc), 12 - abs(p - pc)))
            octave = (lead_note.pitch // 12) * 12
            candidates = [closest_pc + octave - 12, closest_pc + octave, closest_pc + octave + 12]
            final_pitch = min([c for c in candidates if 0 <= c <= 127], key=lambda c: abs(c - lead_note.pitch))

        s_vel = int(np.clip(lead_note.velocity * 1.15, 95, 118))
        
        # Preserva articulação staccato original (sem alongamento artificial forçado)
        dur = max(0.045, lead_note.end - lead_note.start)
        solo_notes.append(
            pretty_midi.Note(
                velocity=s_vel,
                pitch=int(final_pitch),
                start=float(lead_note.start),
                end=float(lead_note.start + dur),
            )
        )

    # Remove duplicatas
    solo_notes.sort(key=lambda n: (n.start, n.pitch))
    cleaned_solo = []
    for n in solo_notes:
        if cleaned_solo and abs(cleaned_solo[-1].start - n.start) < 0.025 and cleaned_solo[-1].pitch == n.pitch:
            continue
        cleaned_solo.append(n)
    solo_notes = cleaned_solo

    # CRUCIAL: Limpa other.mid para NUNCA tocar notas de solo!
    # 'O solo é pra ser feito por apenas 1 piano (no estado atual do programa ele é tocado por dois pianos)'
    if pm_oth and other_midi_path:
        cleaned_oth_notes = []
        for oth_n in pm_oth.instruments[0].notes:
            # Se a nota coincide exatamente com a nota melódica de solo, remove de other para não duplicar
            if any(abs(s.start - oth_n.start) < 0.05 and s.pitch == oth_n.pitch for s in solo_notes):
                continue
            cleaned_oth_notes.append(oth_n)
        pm_oth.instruments[0].notes = cleaned_oth_notes
        pm_oth.write(str(other_midi_path))

    # Cria Instrumento Solo (Canal 8 / Program solo_program)
    solo_inst = pretty_midi.Instrument(program=solo_program, name=solo_name)
    solo_inst.notes = solo_notes

    # Cria Instrumento Piano Base (Canal 2 / Program 0)
    base_notes.sort(key=lambda n: (n.start, n.pitch))
    base_inst = pretty_midi.Instrument(program=inst_piano.program, name="Piano")
    base_inst.notes = base_notes

    # Salva piano_solo.mid
    pm_solo = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
    pm_solo.instruments.append(solo_inst)
    out_solo_p.parent.mkdir(parents=True, exist_ok=True)
    pm_solo.write(str(out_solo_p))

    # Salva piano.mid (base limpa sem notas de solo)
    pm_base = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
    pm_base.instruments.append(base_inst)
    out_piano_p.parent.mkdir(parents=True, exist_ok=True)
    pm_base.write(str(out_piano_p))

    comeco_count = sum(1 for n in solo_notes if any(iv["type"] == "comeco" and iv["start"] <= n.start <= iv["end"] for iv in intervals))
    meio_count = sum(1 for n in solo_notes if any(iv["type"] == "meio" and iv["start"] <= n.start <= iv["end"] for iv in intervals))
    final_count = sum(1 for n in solo_notes if any(iv["type"] == "final" and iv["start"] <= n.start <= iv["end"] for iv in intervals))

    return {
        "solo_notes": len(solo_notes),
        "base_notes": len(base_notes),
        "intervals_count": len(intervals),
        "comeco_notes": comeco_count,
        "meio_notes": meio_count,
        "final_notes": final_count,
        "solo_file": str(out_solo_p),
        "piano_file": str(out_piano_p),
        "status": "success",
    }
