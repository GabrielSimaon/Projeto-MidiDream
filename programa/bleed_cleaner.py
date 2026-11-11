"""Remoção de vazamento acústico (bleed/leakage) e de notas de fundo mais baixas entre faixas."""

from __future__ import annotations

import copy
from pathlib import Path
import numpy as np
import pretty_midi
import soundfile as sf
import librosa


def normalize_audio_stems(stems_dir: str | Path, target_peak_db: float = -1.0) -> dict[str, float]:
    """Normaliza o volume de pico de cada faixa de áudio separada para um nível uniforme (-1.0 dBFS).
    
    Elimina oscilações indesejadas de ganho, remove DC offset e assegura que todas as faixas
    em faixas_instrumentos tenham volume equilibrado, audível e consistente.
    """
    stems_path = Path(stems_dir)
    if not stems_path.is_dir():
        return {}

    target_peak = 10.0 ** (target_peak_db / 20.0)
    results = {}

    for audio_file in sorted(stems_path.iterdir()):
        if not audio_file.is_file() or audio_file.suffix.lower() not in (".wav", ".flac", ".mp3", ".ogg"):
            continue
        try:
            data, orig_sr = sf.read(str(audio_file), always_2d=True, dtype="float32")
            if len(data) == 0:
                continue

            # Remove DC offset em cada canal
            data = data - np.mean(data, axis=0)
            peak = float(np.max(np.abs(data)))

            # Se a faixa tiver sinal perceptível (> -45 dB)
            if peak >= 0.005:
                gain = target_peak / peak
                # Limite de ganho (+24 dB / ~15.8x) para não amplificar chiado de faixas vazias
                gain = min(gain, 15.85)
                norm_data = np.clip(data * gain, -1.0, 1.0)
                sf.write(str(audio_file), norm_data, orig_sr, subtype="PCM_16" if audio_file.suffix.lower() == ".wav" else None)
                results[audio_file.name] = gain
        except Exception as exc:
            print(f"[aviso] Falha ao normalizar faixa {audio_file.name}: {exc}")

    return results


def clean_audio_bleed(stems_dir: str | Path) -> dict[str, bool]:
    """Normaliza e estabiliza o áudio das faixas separadas, eliminando oscilações de volume."""
    norm_res = normalize_audio_stems(stems_dir)
    return {k: True for k in norm_res}


def clean_midi_bleed(midis: dict[str, pretty_midi.PrettyMIDI]) -> dict[str, int]:
    """Remove notas fantasma de instrumentos de fundo nos arquivos MIDI transcritos.
    
    1. Notas de melodia vocal que vazaram para piano, guitarra ou acompanhamento.
    2. Notas graves do baixo que vazaram para piano ou violão.
    3. Acordes duplicados com energia fraca em um dos instrumentos (remoção do secundário).
    """
    removed_counts = {name: 0 for name in midis}

    vocal_notes: list[pretty_midi.Note] = []
    if "vocals" in midis:
        vocal_notes = [n for inst in midis["vocals"].instruments if not inst.is_drum for n in inst.notes]

    bass_notes: list[pretty_midi.Note] = []
    if "bass" in midis:
        bass_notes = [n for inst in midis["bass"].instruments if not inst.is_drum for n in inst.notes]

    drum_attacks: list[float] = []
    if "drums" in midis:
        drum_attacks = [
            round(n.start, 3)
            for inst in midis["drums"].instruments
            if inst.is_drum
            for n in inst.notes
        ]

    # Limpeza de vazamento em introdução: remove vazamento de piano no baixo antes do groove
    piano_notes_ref = []
    if "piano" in midis:
        piano_notes_ref = [n for inst in midis["piano"].instruments if not inst.is_drum for n in inst.notes]

    if "bass" in midis and piano_notes_ref:
        first_drum_groove = 999.0
        if "drums" in midis:
            groove_kicks = [n.start for inst in midis["drums"].instruments if inst.is_drum for n in inst.notes if n.pitch in (35, 36) and n.velocity >= 70]
            if groove_kicks:
                first_drum_groove = min(groove_kicks)

        for b_inst in midis["bass"].instruments:
            if b_inst.is_drum:
                continue
            clean_b = []
            for bn in b_inst.notes:
                if bn.start < first_drum_groove and bn.start < 16.0:
                    coincides_with_piano = any(abs(bn.start - pn.start) < 0.08 or (pn.start <= bn.start <= pn.end) for pn in piano_notes_ref)
                    if coincides_with_piano and bn.velocity < 115:
                        removed_counts["bass"] += 1
                        continue
                clean_b.append(bn)
            b_inst.notes = clean_b

    # 1. Remove vazamento vocal e de baixo no acompanhamento (piano, guitarra, other)
    for stem_name in ("piano", "guitar", "acoustic_guitar", "electric_guitar", "other"):
        if stem_name not in midis:
            continue
        
        midi_obj = midis[stem_name]
        for inst in midi_obj.instruments:
            if inst.is_drum:
                continue
            
            kept_notes: list[pretty_midi.Note] = []
            for n in inst.notes:
                is_bleed = False
                n_mid = (n.start + n.end) / 2.0
                n_dur = n.end - n.start

                # Regra A: Vazamento de Baixo (apenas ruído residual muito fraco no subgrave abaixo de C3)
                if n.pitch < 48 and n.velocity < 42 and bass_notes:
                    for b in bass_notes:
                        if b.start <= n_mid <= b.end or n.start <= (b.start + b.end) / 2.0 <= n.end:
                            if n.pitch % 12 == b.pitch % 12 and n.velocity < b.velocity * 0.45:
                                is_bleed = True
                                break
                    if is_bleed:
                        removed_counts[stem_name] += 1
                        continue

                # Regra B: Vazamento Vocal (apenas ruído residual fraco, nunca notas harmônicas normais)
                if vocal_notes and n.velocity < 38:
                    for v in vocal_notes:
                        if v.start <= n_mid <= v.end or n.start <= (v.start + v.end) / 2.0 <= n.end:
                            overlap_start = max(n.start, v.start)
                            overlap_end = min(n.end, v.end)
                            overlap = max(0.0, overlap_end - overlap_start)
                            if overlap >= min(n_dur, v.end - v.start) * 0.60:
                                if n.pitch % 12 == v.pitch % 12 and n.velocity < v.velocity * 0.40:
                                    is_bleed = True
                                    break
                    if is_bleed:
                        removed_counts[stem_name] += 1
                        continue

                # Regra C: Vazamento de Bateria (transiente curto < 0.11s coincidindo com ataque de bateria)
                if drum_attacks and n_dur < 0.11 and stem_name in ("piano", "guitar", "other"):
                    if any(abs(n.start - dt) <= 0.025 for dt in drum_attacks):
                        removed_counts[stem_name] += 1
                        continue

                kept_notes.append(n)
            inst.notes = kept_notes

    # 2. Remoção de vazamento cruzado Piano vs Guitarra
    # Se ambos detectaram a mesma nota quase no mesmo instante, mas um está muito mais fraco
    has_piano = "piano" in midis and any(len(i.notes) for i in midis["piano"].instruments if not i.is_drum)
    has_guitar = any(k in midis for k in ("guitar", "acoustic_guitar", "electric_guitar"))
    guitar_key = next((k for k in ("guitar", "acoustic_guitar", "electric_guitar") if k in midis), None)

    if has_piano and has_guitar and guitar_key:
        piano_notes = [n for i in midis["piano"].instruments if not i.is_drum for n in i.notes]
        guitar_inst = next((i for i in midis[guitar_key].instruments if not i.is_drum), None)
        piano_inst = next((i for i in midis["piano"].instruments if not i.is_drum), None)

        if guitar_inst and piano_inst:
            guitar_notes = guitar_inst.notes
            clean_piano = []
            clean_guitar = []
            
            # Verifica vazamento de piano na guitarra e vice-versa
            for pn in piano_notes:
                leak_in_piano = False
                for gn in guitar_notes:
                    if pn.pitch == gn.pitch and abs(pn.start - gn.start) < 0.04:
                        if pn.velocity < gn.velocity * 0.65:
                            leak_in_piano = True
                            break
                if leak_in_piano:
                    removed_counts["piano"] += 1
                else:
                    clean_piano.append(pn)

            for gn in guitar_notes:
                leak_in_guitar = False
                for pn in piano_notes:
                    if gn.pitch == pn.pitch and abs(gn.start - pn.start) < 0.04:
                        if gn.velocity < pn.velocity * 0.65:
                            leak_in_guitar = True
                            break
                if leak_in_guitar:
                    removed_counts[guitar_key] += 1
                else:
                    clean_guitar.append(gn)

            piano_inst.notes = clean_piano
            guitar_inst.notes = clean_guitar

    return removed_counts
