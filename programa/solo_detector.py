"""Detecção e extração dedicada de solos de teclado/piano (PianoSolo).

Atende estritamente à solicitação do usuário:
"preciso que faça um piano somente para solos, pq eles estão se misturando no meio da musica,
mas esse piano vai ter unica e exclusivamente a função de fazer um solo. Quero que o midiDream
tenha a capacidade de detectar o solo no começo, no meio ou no final da musica e designe a função ao 'PianoSolo'".

Princípios:
1. Detecta intervalos de solo no Começo (Introdução), Meio (Interlúdios/gaps vocais/seções de solo) e Final (Encerramento).
2. Extrai única e exclusivamente as frases melódicas e notas virtuosas de solo para a faixa 'Piano Solo' (Canal 8).
3. Mantém 'Piano Solo' 100% silencioso durante as partes cantadas normais (sem misturar com base de acompanhamento).
4. Remove as notas de solo da base de acompanhamento (piano.mid), evitando que solo e base fiquem embolados.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Sequence
import numpy as np
import pretty_midi


def detect_solo_intervals(
    duration: float,
    detected_words: Sequence[dict[str, Any]] | None = None,
    guide_text: str | None = None,
    min_middle_gap: float = 2.4,
) -> list[dict[str, Any]]:
    """Identifica os trechos temporais onde ocorrem solos: Começo, Meio e Final da música.

    Retorna uma lista de dicionários com:
    - start: início em segundos
    - end: término em segundos
    - type: 'comeco' | 'meio' | 'final'
    - label: descrição amigável
    """
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
        # Sem transcrição vocal: primeiros 12 a 16 segundos são padrão de introdução
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

    # Se houver marcações textuais como [Solo], [Interlúdio], [Instrumental]
    if guide_text:
        import re
        for m in re.finditer(r"\[(\d+:\d+(?:\.\d+)?\s+)?(solo|interl[uú]dio|instrumental|ponte|intro)\]", guide_text, re.IGNORECASE):
            # Se tiver timestamp explícito
            pass

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
    """Verifica se um instante temporal pertence a algum dos trechos de solo."""
    for iv in solo_intervals:
        if (iv["start"] - margin) <= time_sec <= (iv["end"] + margin):
            return True
    return False


def split_piano_solo(
    piano_midi_path: str | Path,
    other_midi_path: str | Path | None = None,
    solo_intervals: Sequence[dict[str, Any]] | None = None,
    output_solo_path: str | Path | None = None,
    output_piano_path: str | Path | None = None,
    bpm: float = 120.0,
    solo_program: int = 0,
) -> dict[str, Any]:
    """Separa o piano em duas faixas independentes e especializadas:

    1. 'piano_solo.mid' (PianoSolo): Contém ÚNICA E EXCLUSIVAMENTE os solos de introdução,
       meio e finalização. Fica silencioso nas partes cantadas.
    2. 'piano.mid' (Piano Base): Acompanhamento harmônico e rítmico, limpo e sem notas
       de solo emboladas no meio da música.
    """
    piano_p = Path(piano_midi_path)
    if not piano_p.is_file():
        return {"solo_notes": 0, "base_notes": 0, "status": "no_piano_file"}

    out_solo_p = Path(output_solo_path) if output_solo_path else piano_p.with_name("piano_solo.mid")
    out_piano_p = Path(output_piano_path) if output_piano_path else piano_p

    pm_piano = pretty_midi.PrettyMIDI(str(piano_p))
    inst_piano = pm_piano.instruments[0] if pm_piano.instruments else None
    if not inst_piano or not inst_piano.notes:
        return {"solo_notes": 0, "base_notes": 0, "status": "empty_piano"}

    # Carrega também other.mid se existir para capturar possíveis notas melódicas de solo vazadas
    other_notes = []
    if other_midi_path:
        oth_p = Path(other_midi_path)
        if oth_p.is_file():
            try:
                pm_oth = pretty_midi.PrettyMIDI(str(oth_p))
                if pm_oth.instruments:
                    other_notes = pm_oth.instruments[0].notes
            except Exception:
                pass

    intervals = list(solo_intervals or [])
    if not intervals:
        dur = max((n.end for n in inst_piano.notes), default=60.0)
        intervals = detect_solo_intervals(duration=dur)

    all_piano_notes = sorted(inst_piano.notes, key=lambda n: (n.start, n.pitch))
    solo_notes: list[pretty_midi.Note] = []
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
            # FORA DO SOLO: Todas as notas vão 100% para a base!
            # PianoSolo permanece estritamente silencioso aqui.
            for n in cluster:
                base_notes.append(n)
        else:
            # DENTRO DO INTERVALO DE SOLO (Começo, Meio ou Final):
            # 1. Se for monofônico (uma única nota tocando na melodia):
            if len(cluster) == 1:
                n = cluster[0]
                if n.pitch >= 55:  # Nota melódica (Sol 3 para cima)
                    # Dá ganho e brilho à nota de solo
                    s_vel = int(np.clip(n.velocity * 1.15, 95, 118))
                    solo_notes.append(
                        pretty_midi.Note(velocity=s_vel, pitch=n.pitch, start=n.start, end=n.end)
                    )
                else:
                    # Nota grave de apoio de baixo
                    base_notes.append(n)
            else:
                # Polifônico durante o solo:
                # A nota mais aguda (linha de soprano/melodia condutora) é o SOLO!
                max_pitch = max(n.pitch for n in cluster)
                for n in cluster:
                    if n.pitch == max_pitch and n.pitch >= 60:
                        s_vel = int(np.clip(n.velocity * 1.15, 95, 118))
                        solo_notes.append(
                            pretty_midi.Note(velocity=s_vel, pitch=n.pitch, start=n.start, end=n.end)
                        )
                    else:
                        # As outras notas formam o acompanhamento harmônico
                        base_notes.append(n)

    # Verifica também em other_notes se há licks de solo nos intervalos que não estavam no piano
    if other_notes:
        for oth_n in other_notes:
            if is_in_solo_window(oth_n.start, intervals) and oth_n.pitch >= 65:
                # Verifica se já não existe nota de solo nesse mesmo tempo e altura
                is_duplicate = any(
                    abs(s.start - oth_n.start) < 0.05 and abs(s.pitch - oth_n.pitch) == 0
                    for s in solo_notes
                )
                if not is_duplicate:
                    s_vel = int(np.clip(oth_n.velocity * 1.15, 92, 115))
                    solo_notes.append(
                        pretty_midi.Note(velocity=s_vel, pitch=oth_n.pitch, start=oth_n.start, end=oth_n.end)
                    )

    # Ordena e limpa solo_notes
    solo_notes.sort(key=lambda n: (n.start, n.pitch))
    cleaned_solo = []
    for n in solo_notes:
        if cleaned_solo and abs(cleaned_solo[-1].start - n.start) < 0.02 and cleaned_solo[-1].pitch == n.pitch:
            continue
        cleaned_solo.append(n)
    solo_notes = cleaned_solo

    # Cria Instrumento Piano Solo (Canal 8 / Program solo_program)
    solo_inst = pretty_midi.Instrument(program=solo_program, name="Piano Solo")
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

    # Salva piano.mid (base limpa)
    pm_base = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
    pm_base.instruments.append(base_inst)
    out_piano_p.parent.mkdir(parents=True, exist_ok=True)
    pm_base.write(str(out_piano_p))

    # Contagem de solos por tipo
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
