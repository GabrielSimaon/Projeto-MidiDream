"""Separação e aprimoramento multi-instrumental dos stems de acompanhamento.

Atende estritamente às diretrizes do usuário:
1. 'na pasta motor midi o mid ta quase certo, o midi do motor mid tem q separar os instrumentos
    pq o piano 1 ta fazendo um monte de instrumento'.
2. NUNCA inventa notas aleatórias / sintéticas: todas as notas vêm estritamente da transcrição
    real gravada no áudio (motor_midi / other.mid / piano 1).
3. 'O solo é pra ser feito por apenas 1 piano, defina apenas 1 teclado pro solo e o outro pra acompanhar'.
4. 'o piano que vai acompanhar vai tocar a nota sem estacar(dar petelecos) quero que ele segure um pouco'.
5. 'ja o piano do solo tem liberdade pra flutuar em estacato, porém sem tocar notas que n existam'.
6. 'Na música há violões porem no mid nao há, o violão tem que ser simples também'.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Sequence

import numpy as np
import pretty_midi

from midi_timing import make_instrumental
from lyrics import remap_channels
from auto_mix import apply_auto_mix, calculate_relative_volumes
from solo_detector import detect_solo_intervals, is_in_solo_window


def separate_accompaniment_instruments(
    input_notes: list[pretty_midi.Note],
    solo_intervals: Sequence[dict] = (),
    bpm: float = 120.0,
) -> dict[str, list[pretty_midi.Note]]:
    """Separa as notas reais do acompanhamento em faixas de instrumentos dedicados.

    Nenhuma nota é inventada. Todas as notas derivam do áudio original transcrito.
    """
    if not input_notes:
        return {
            "piano_solo": [],
            "piano_base": [],
            "guitar": [],
            "accordion": [],
            "pad": [],
        }

    sorted_notes = sorted(input_notes, key=lambda n: (n.start, n.pitch))

    # Agrupa notas por ataques simultâneos (cluster de acordes / dedilhado em janela de 35ms)
    clusters: list[list[pretty_midi.Note]] = []
    for n in sorted_notes:
        if not clusters or (n.start - clusters[-1][0].start) > 0.035:
            clusters.append([n])
        else:
            clusters[-1].append(n)

    solo_notes: list[pretty_midi.Note] = []
    piano_notes: list[pretty_midi.Note] = []
    guitar_notes: list[pretty_midi.Note] = []
    accordion_notes: list[pretty_midi.Note] = []
    pad_notes: list[pretty_midi.Note] = []

    def _in_solo(t: float) -> bool:
        return any((iv.get("start", 0) - 0.08) <= t <= (iv.get("end", 0) + 0.08) for iv in solo_intervals)

    for c in clusters:
        t = c[0].start
        c_sorted = sorted(c, key=lambda x: x.pitch)
        k = len(c_sorted)
        is_solo_win = _in_solo(t)
        top_n = c_sorted[-1]

        # 1. Extração do Solo Melódico (Piano Solo / Lead)
        # Em janelas de solo (introdução, interlúdios, encerramento):
        # A voz melódica mais aguda (pitch >= 62) é a melodia de solo!
        # Fora de solo: fraseados/fills destacados nos agudos (pitch >= 72 com k <= 2, ou pitch >= 74)
        is_solo_note = False
        if is_solo_win and top_n.pitch >= 62:
            is_solo_note = True
        elif not is_solo_win and top_n.pitch >= 72 and (k <= 2 or top_n.pitch >= 74):
            is_solo_note = True

        if is_solo_note:
            solo_notes.append(
                pretty_midi.Note(
                    velocity=min(127, int(top_n.velocity * 1.15)),
                    pitch=top_n.pitch,
                    start=top_n.start,
                    end=top_n.end,
                )
            )
            base_candidates = c_sorted[:-1]
        else:
            base_candidates = c_sorted

        if not base_candidates:
            continue

        # 2. Distribuição da base de acompanhamento entre os instrumentos:
        for n in base_candidates:
            dur = n.end - n.start

            # A. Violão (Acoustic Guitar) - levada rítmica gravada (batidas/palhetadas na tessitura 44-72)
            if 44 <= n.pitch <= 72 and n.velocity >= 32:
                guitar_notes.append(
                    pretty_midi.Note(
                        velocity=n.velocity,
                        pitch=n.pitch,
                        start=n.start,
                        end=n.end,
                    )
                )

            # B. Piano de Acompanhamento (Base sustentada, sem petelecos)
            if 48 <= n.pitch <= 76 and n.velocity >= 34:
                # 'o piano que vai acompanhar vai tocar a nota sem estacar(dar petelecos) quero que ele segure um pouco'
                p_dur = max(dur, 0.45)
                piano_notes.append(
                    pretty_midi.Note(
                        velocity=int(np.clip(n.velocity * 0.95, 62, 88)),
                        pitch=n.pitch,
                        start=n.start,
                        end=n.start + p_dur,
                    )
                )

            # C. Acordeon / Sanfona (Acordes de fole sustentados)
            if dur >= 0.38 and 52 <= n.pitch <= 78 and n.velocity >= 35:
                accordion_notes.append(
                    pretty_midi.Note(
                        velocity=int(np.clip(n.velocity * 0.92, 60, 84)),
                        pitch=n.pitch,
                        start=n.start,
                        end=n.end,
                    )
                )

            # D. Synth Pad / Cama Harmônica (Camada de sustentação)
            if dur >= 0.50 and 48 <= n.pitch <= 72:
                pad_notes.append(
                    pretty_midi.Note(
                        velocity=int(np.clip(n.velocity * 0.85, 52, 72)),
                        pitch=n.pitch,
                        start=n.start,
                        end=n.end,
                    )
                )

    # Ordena as notas de cada instrumento
    for note_list in (solo_notes, piano_notes, guitar_notes, accordion_notes, pad_notes):
        note_list.sort(key=lambda x: (x.start, x.pitch))

    return {
        "piano_solo": solo_notes,
        "piano_base": piano_notes,
        "guitar": guitar_notes,
        "accordion": accordion_notes,
        "pad": pad_notes,
    }


def enhance_keyboards(
    midi_dir: str | Path,
    bpm: float = 120.0,
    chords: Sequence[tuple[float, str]] = (),
    duration: float = 0.0,
    key: str | None = None,
    solo_intervals: Sequence[dict] = (),
    allow_synthetic: bool = False,
    engine_midi_dir: str | Path | None = None,
    solo_instrument: str = "piano",
) -> dict:
    """Separa e orquestra o arquivo completo e os stems a partir do MIDI real do motor.

    Separa o 'Piano 1' (other.mid) que contém múltiplos instrumentos misturados em:
    - Piano Solo (Canal 8 / Program 0)
    - Piano Base (Canal 2 / Program 0)
    - Violão / Acoustic Guitar (Canal 3 / Program 25)
    - Sanfona / Accordion (Canal 5 / Program 21)
    - Synth Pad (Canal 4 / Program 88)
    e consolida completo.mid, instrumental.mid e karaoke.mid/kar.
    """
    midi_dir = Path(midi_dir)
    source_dir = Path(engine_midi_dir) if engine_midi_dir and Path(engine_midi_dir).is_dir() else midi_dir

    # Se solo_intervals não foram passados, tenta carregar palavras.json ou inferir
    if not solo_intervals:
        palavras_p = midi_dir.parent / "palavras.json"
        vocal_words = []
        if palavras_p.is_file():
            try:
                words_data = json.loads(palavras_p.read_text(encoding="utf-8"))
                vocal_words = [w for w in words_data if w.get("text", "").strip() not in ("[QUEBRA]", "")]
            except Exception:
                pass
        solo_intervals = detect_solo_intervals(duration=duration, detected_words=vocal_words)

    other_path = midi_dir / "other.mid"
    piano_path = midi_dir / "piano.mid"
    guitar_path = midi_dir / "guitar.mid"
    accordion_path = midi_dir / "accordion.mid"
    pad_path = midi_dir / "pad.mid"
    solo_path = midi_dir / "piano_solo.mid"
    combined_path = midi_dir / "completo.mid"
    instrumental_path = midi_dir / "instrumental.mid"
    karaoke_path = midi_dir / "karaoke.mid"
    kar_path = midi_dir / "karaoke.kar"

    # Coleta todas as notas de acompanhamento reais a serem separadas diretamente da base motor_midi
    poly_notes: list[pretty_midi.Note] = []

    # 1. Do arquivo other.mid da base motor_midi
    src_other = source_dir / "other.mid"
    if src_other.is_file():
        try:
            oth_pm = pretty_midi.PrettyMIDI(str(src_other))
            for inst in oth_pm.instruments:
                if not inst.is_drum:
                    poly_notes.extend(inst.notes)
        except Exception:
            pass

    # 2. Do piano.mid da base se já existir notas
    src_piano = source_dir / "piano.mid"
    if src_piano.is_file():
        try:
            p_pm = pretty_midi.PrettyMIDI(str(src_piano))
            for inst in p_pm.instruments:
                if not inst.is_drum and len(inst.notes) > len(poly_notes):
                    poly_notes = inst.notes
        except Exception:
            pass

    # 3. Se ainda vazio, lê do completo.mid da base
    src_comb = source_dir / "completo.mid"
    if not poly_notes and src_comb.is_file():
        try:
            c_pm = pretty_midi.PrettyMIDI(str(src_comb))
            for inst in c_pm.instruments:
                if not inst.is_drum and ("other" in inst.name.lower() or "acompanhamento" in inst.name.lower() or (inst.program == 0 and len(inst.notes) >= 50)):
                    poly_notes = inst.notes
                    break
        except Exception:
            pass

    # Executa a separação fiel dos instrumentos a partir das notas reais
    separated = separate_accompaniment_instruments(
        poly_notes, solo_intervals=solo_intervals, bpm=bpm
    )

    solo_notes = separated["piano_solo"]
    piano_notes = separated["piano_base"]
    guitar_notes = separated["guitar"]
    accordion_notes = separated["accordion"]
    pad_notes = separated["pad"]

    # Configuração do instrumento do solo melódico
    solo_norm = str(solo_instrument or "piano").lower().strip()
    if any(g in solo_norm for g in ("electric", "eletrica", "elétrica", "guitarra", "guitar")):
        solo_prog = 27
        solo_name = "Electric Guitar Solo"
    elif any(a in solo_norm for a in ("violao", "violão", "acoustic", "acustico", "acústico")):
        solo_prog = 25
        solo_name = "Acoustic Guitar Solo"
    else:
        solo_prog = 0
        solo_name = "Piano Solo"

    # Grava individualmente cada faixa MIDI
    def _write_instrument_midi(notes_list: list[pretty_midi.Note], prog: int, name: str, out_p: Path) -> None:
        pm = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
        inst = pretty_midi.Instrument(program=prog, name=name)
        inst.notes = notes_list
        pm.instruments.append(inst)
        pm.write(str(out_p))
        remap_channels(out_p)

    _write_instrument_midi(piano_notes, 0, "Piano", piano_path)
    _write_instrument_midi(solo_notes, solo_prog, solo_name, solo_path)
    if solo_prog != 0:
        _write_instrument_midi(solo_notes, solo_prog, solo_name, midi_dir / "guitar_solo.mid")
    _write_instrument_midi(guitar_notes, 25, "Acoustic Guitar", guitar_path)
    _write_instrument_midi(accordion_notes, 21, "Accordion", accordion_path)
    _write_instrument_midi(pad_notes, 88, "Synth Pad", pad_path)

    # Atualiza completo.mid
    if combined_path.is_file():
        comb = pretty_midi.PrettyMIDI(str(combined_path))

        # Preserva faixas rítmicas e vocais originais da base motor_midi com fidelidade absoluta
        src_comb_f = source_dir / "completo.mid"
        ref_comb = pretty_midi.PrettyMIDI(str(src_comb_f)) if src_comb_f.is_file() else comb

        preserved_insts = [
            i for i in ref_comb.instruments
            if i.is_drum
            or "drum" in i.name.lower()
            or "chimbal" in i.name.lower()
            or "bateria" in i.name.lower()
            or "bass" in i.name.lower()
            or "baixo" in i.name.lower()
            or any(v in i.name.lower() for v in ("vocal", "choir", "voice", "voz"))
        ]

        new_insts = []
        # 1. Piano Base
        if piano_notes:
            p_inst = pretty_midi.Instrument(program=0, name="Piano")
            p_inst.notes = piano_notes
            new_insts.append(p_inst)

        # 2. Solo Melódico (Piano Solo, Guitarra Elétrica Solo ou Violão Solo)
        if solo_notes:
            s_inst = pretty_midi.Instrument(program=solo_prog, name=solo_name)
            s_inst.notes = solo_notes
            new_insts.append(s_inst)

        # 3. Bateria e Percussão
        for inst in preserved_insts:
            if inst.is_drum or "drum" in inst.name.lower() or "bateria" in inst.name.lower():
                new_insts.append(inst)

        # 4. Violão (Acoustic Guitar)
        if guitar_notes:
            g_inst = pretty_midi.Instrument(program=25, name="Acoustic Guitar")
            g_inst.notes = guitar_notes
            new_insts.append(g_inst)

        # 5. Sanfona (Accordion)
        if accordion_notes:
            a_inst = pretty_midi.Instrument(program=21, name="Accordion")
            a_inst.notes = accordion_notes
            new_insts.append(a_inst)

        # 6. Baixo (Bass)
        for inst in preserved_insts:
            if "bass" in inst.name.lower() or "baixo" in inst.name.lower() or (inst.program in range(32, 40) and not inst.is_drum):
                new_insts.append(inst)

        # 7. Synth Pad
        if pad_notes:
            pad_inst = pretty_midi.Instrument(program=88, name="Synth Pad")
            pad_inst.notes = pad_notes
            new_insts.append(pad_inst)

        # 8. Vocal (Choir Aahs)
        for inst in preserved_insts:
            if any(v in inst.name.lower() for v in ("vocal", "choir", "voice", "voz")):
                new_insts.append(inst)

        comb.instruments = new_insts
        comb.write(str(combined_path))
        remap_channels(combined_path)

        # Atualiza instrumental.mid
        if instrumental_path.parent.exists():
            make_instrumental(combined_path, instrumental_path)

        # Atualiza karaoke.mid e karaoke.kar preservando a letra sincronizada
        silabas_p = midi_dir.parent / "silabas.json"
        if silabas_p.is_file():
            try:
                from lyrics import export_karaoke
                rows = json.loads(silabas_p.read_text(encoding="utf-8"))
                export_karaoke(combined_path, rows, midi_dir)
                if karaoke_path.is_file():
                    kar_path.write_bytes(karaoke_path.read_bytes())
            except Exception:
                pass

    # Aplica mixagem equilibrada com CC7 e dinâmica expressiva
    try:
        volumes = calculate_relative_volumes(midi_dir)
        apply_auto_mix(midi_dir, volumes)
    except Exception:
        pass

    return {
        "solo_instrument": solo_name,
        "solo_program": solo_prog,
        "piano_notes": len(piano_notes),
        "piano_solo_notes": len(solo_notes),
        "guitar_notes": len(guitar_notes),
        "accordion_notes": len(accordion_notes),
        "pad_notes": len(pad_notes),
        "total_source_notes": len(poly_notes),
        "status": "real_notes_separated_into_instruments",
    }
