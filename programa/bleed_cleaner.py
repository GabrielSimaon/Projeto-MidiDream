"""Remoção de vazamento acústico (bleed/leakage) e de notas de fundo mais baixas entre faixas."""

from __future__ import annotations

import copy
from pathlib import Path
import numpy as np
import pretty_midi
import soundfile as sf
import librosa


def clean_audio_bleed(stems_dir: str | Path) -> dict[str, bool]:
    """Aplica soft gating dinâmico nos stems para atenuar vazamento de voz e bateria no fundo.
    
    Identifica trechos onde um instrumento secundário (ex: piano, guitarra) está em repouso
    ou muito baixo, mas registra resíduo de voz ou bateria que sangrou durante a separação.
    """
    stems_path = Path(stems_dir)
    if not stems_path.is_dir():
        return {}

    stem_names = ["vocals", "drums", "bass", "piano", "guitar", "other"]
    audio_files: dict[str, Path] = {}
    for name in stem_names:
        for ext in (".wav", ".flac"):
            p = stems_path / f"{name}{ext}"
            if p.is_file():
                audio_files[name] = p
                break

    if "vocals" not in audio_files and "drums" not in audio_files:
        return {}

    # Carrega os envelopes dos stems principais para referência de vazamento
    sr = 22050
    hop = 512
    main_envelopes = {}
    for key in ("vocals", "drums", "bass"):
        if key in audio_files:
            try:
                y, _ = librosa.load(str(audio_files[key]), sr=sr, mono=True)
                rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
                peak = float(np.quantile(rms, 0.95)) if len(rms) else 0.0
                main_envelopes[key] = (rms, peak, len(y))
            except Exception:
                pass

    report = {}
    # Aplica atenuação nos instrumentos de acompanhamento
    for inst_name in ("piano", "guitar", "other"):
        if inst_name not in audio_files:
            continue
        inst_file = audio_files[inst_name]
        try:
            y_inst, orig_sr = sf.read(str(inst_file), always_2d=True)
            mono_inst = np.mean(y_inst, axis=1) if y_inst.shape[1] > 1 else y_inst[:, 0]
            
            # Reamostra temporariamente para calcular RMS compatível
            mono_resampled = librosa.resample(mono_inst, orig_sr=orig_sr, target_sr=sr) if orig_sr != sr else mono_inst
            rms_inst = librosa.feature.rms(y=mono_resampled, frame_length=1024, hop_length=hop)[0]
            peak_inst = float(np.quantile(rms_inst, 0.95)) if len(rms_inst) else 0.0
            if peak_inst <= 1e-6:
                continue

            # Máscara de atenuação
            attenuate_mask = np.zeros(len(rms_inst), dtype=bool)

            # Verifica vazamento de voz: trecho onde voz canta forte e o instrumento está residual
            if "vocals" in main_envelopes:
                rms_vox, peak_vox, _ = main_envelopes["vocals"]
                n_frames = min(len(rms_inst), len(rms_vox))
                if peak_vox > 1e-5:
                    is_vox_strong = rms_vox[:n_frames] > peak_vox * 0.18
                    is_inst_residual = rms_inst[:n_frames] < peak_inst * 0.10
                    # Vazamento confirmado quando voz domina o canal do instrumento
                    attenuate_mask[:n_frames] |= (is_vox_strong & is_inst_residual & (rms_inst[:n_frames] < rms_vox[:n_frames] * 0.35))

            if np.count_nonzero(attenuate_mask) > 0:
                # Converte máscara de quadros para amostras no áudio original
                ratio = float(orig_sr) / float(sr)
                hop_orig = int(round(hop * ratio))
                mask_samples = np.repeat(attenuate_mask, hop_orig)
                if len(mask_samples) < len(y_inst):
                    mask_samples = np.pad(mask_samples, (0, len(y_inst) - len(mask_samples)), constant_values=False)
                else:
                    mask_samples = mask_samples[:len(y_inst)]

                # Suaviza a curva de transição para evitar cliques
                gain = np.ones(len(y_inst), dtype=np.float32)
                gain[mask_samples] = 0.05
                import scipy.ndimage
                smooth_radius = int(orig_sr * 0.02)  # 20 ms
                if smooth_radius > 1:
                    gain = scipy.ndimage.gaussian_filter1d(gain, sigma=smooth_radius)

                # Aplica o ganho em todos os canais
                y_clean = y_inst * gain[:, np.newaxis]
                sf.write(str(inst_file), y_clean, orig_sr, subtype="PCM_16")
                report[inst_name] = True
        except Exception:
            report[inst_name] = False

    return report


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

                # Regra A: Vazamento de Baixo (grave <= 55 que coincide com nota de baixo)
                if n.pitch <= 55 and bass_notes:
                    for b in bass_notes:
                        # Se coincide no tempo e altura (ou oitava)
                        if b.start <= n_mid <= b.end or n.start <= (b.start + b.end) / 2.0 <= n.end:
                            if n.pitch % 12 == b.pitch % 12:
                                # Se o baixo é a fonte real dessa nota grave
                                is_bleed = True
                                break
                    if is_bleed:
                        removed_counts[stem_name] += 1
                        continue

                # Regra B: Vazamento Vocal
                if vocal_notes:
                    for v in vocal_notes:
                        if v.start <= n_mid <= v.end or n.start <= (v.start + v.end) / 2.0 <= n.end:
                            # Se colide no tempo com a voz
                            overlap_start = max(n.start, v.start)
                            overlap_end = min(n.end, v.end)
                            overlap = max(0.0, overlap_end - overlap_start)
                            if overlap >= min(n_dur, v.end - v.start) * 0.50:
                                # Mesma nota ou mesma oitava da melodia vocal
                                if n.pitch % 12 == v.pitch % 12:
                                    # Se a velocidade no acompanhamento não for muito mais forte que a voz
                                    if n.velocity <= v.velocity * 1.05:
                                        is_bleed = True
                                        break
                    if is_bleed:
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
