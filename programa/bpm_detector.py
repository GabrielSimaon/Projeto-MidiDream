"""Detecção automática e de alta precisão de BPM e grade rítmica a partir do áudio."""

from __future__ import annotations

import math
from pathlib import Path
import numpy as np
import librosa
import scipy.stats
import scipy.ndimage


def detect_precise_bpm(
    audio_path: str | Path,
    drums_path: str | Path | None = None,
    *,
    sample_rate: int = 22050,
    target_window_seconds: float = 90.0,
) -> dict:
    """Calcula o BPM com máxima precisão, analisando bateria (se houver) ou a mixagem.
    
    Combina:
    1. Envelope de força de ataque (onset strength) multi-banda.
    2. Estimativa de andamento com prior musical centrado na faixa 80-160 BPM para evitar oitavas erradas.
    3. Rastreamento dinâmico de batidas (beat tracking).
    4. Regressão linear sobre os instantes das batidas para extrair o período e âncora inicial exatos.
    
    Retorna dict com 'bpm', 'anchor' (segundos do primeiro pulso) e 'beat_times'.
    """
    chosen_path = None
    if drums_path:
        p = Path(drums_path)
        if p.is_file() and p.stat().st_size > 1000:
            chosen_path = p
            
    if not chosen_path:
        chosen_path = Path(audio_path)
        
    if not chosen_path.is_file():
        return {"bpm": 120.0, "anchor": 0.0, "beat_times": np.array([])}

    try:
        duration = float(librosa.get_duration(path=str(chosen_path)))
    except Exception:
        duration = 0.0

    offset = 0.0
    load_duration = None
    if duration > target_window_seconds + 10.0:
        # Pega a seção central mais estável da música (evita introduções lentas/fades)
        offset = max(5.0, (duration - target_window_seconds) / 2.0)
        load_duration = target_window_seconds

    try:
        y, sr = librosa.load(str(chosen_path), sr=sample_rate, mono=True, offset=offset, duration=load_duration)
    except Exception:
        return {"bpm": 120.0, "anchor": 0.0, "beat_times": np.array([])}

    if len(y) < sr * 5 or np.max(np.abs(y), initial=0.0) < 1e-4:
        # Áudio curto ou silencioso, tenta o início sem offset
        try:
            y, sr = librosa.load(str(chosen_path), sr=sample_rate, mono=True, duration=60.0)
            offset = 0.0
        except Exception:
            return {"bpm": 120.0, "anchor": 0.0, "beat_times": np.array([])}

    hop_length = 256
    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length, aggregate=np.median)
    except Exception:
        return {"bpm": 120.0, "anchor": 0.0, "beat_times": np.array([])}

    if onset_env.sum() <= 1e-6:
        return {"bpm": 120.0, "anchor": 0.0, "beat_times": np.array([])}

    # 1. Busca os candidatos de andamento via autocorrelação e beat_tracking sem viés restritivo
    ac = librosa.autocorrelate(onset_env, max_size=int(4.0 * sr / hop_length))
    bpms_ac = 60.0 / (np.arange(1, len(ac)) * hop_length / sr)
    valid_mask = (bpms_ac >= 65.0) & (bpms_ac <= 195.0)
    
    candidates = []
    if np.any(valid_mask):
        ac_valid = ac[1:][valid_mask]
        bpms_valid = bpms_ac[valid_mask]
        
        # Encontra picos na autocorrelação
        import scipy.signal
        peaks, _ = scipy.signal.find_peaks(ac_valid, distance=6, prominence=0.03 * float(ac_valid.max()))
        if len(peaks) > 0:
            sorted_peaks = peaks[np.argsort(ac_valid[peaks])[::-1]]
            for p in sorted_peaks[:4]:
                candidates.append(float(bpms_valid[p]))

    # Adiciona também a estimativa livre do beat_track do librosa
    try:
        tempo_bt, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop_length)
        tempo_bt_val = float(np.atleast_1d(tempo_bt)[0])
        if 65.0 <= tempo_bt_val <= 195.0:
            candidates.append(tempo_bt_val)
    except Exception:
        pass

    if not candidates:
        candidates = [120.0]

    # Remove duplicatas próximas (diferença < 2 BPM)
    unique_candidates = []
    for c in candidates:
        if not any(abs(c - u) < 2.0 for u in unique_candidates):
            unique_candidates.append(c)

    # 2. Testa cada candidato de BPM rastreando os pulsos reais e medindo o erro de fase
    best_candidate = None
    min_std = 999.0
    best_anchor = 0.0
    best_beat_times = np.array([])

    for cand in unique_candidates:
        try:
            _, test_beats = librosa.beat.beat_track(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=hop_length,
                start_bpm=cand,
                tightness=120,
                trim=False,
            )
        except Exception:
            continue

        if len(test_beats) >= 8:
            b_times = librosa.frames_to_time(test_beats, sr=sr, hop_length=hop_length) + offset
            intervals = np.diff(b_times)
            med_ioi = float(np.median(intervals))
            if med_ioi > 0.15:
                # Regressão linear sobre os pulsos
                indices = np.arange(len(b_times))
                slope, intercept = np.polyfit(indices, b_times, 1)
                bpm_eval = 60.0 / slope
                residuals = b_times - (slope * indices + intercept)
                std_res = float(np.std(residuals))

                # Prefere candidatos com andamento de dança natural e mínimo erro de fase
                # Penaliza levemente sub-múltiplos como 66 ou 88 se houver um andamento limpo em 130-140
                score = std_res
                if bpm_eval < 95.0 and any(115.0 <= u <= 165.0 for u in unique_candidates):
                    score += 0.05

                if score < min_std and 60.0 <= bpm_eval <= 220.0:
                    min_std = score
                    best_candidate = bpm_eval
                    period = 60.0 / bpm_eval
                    best_anchor = float(intercept % period)
                    best_beat_times = b_times

    final_bpm = best_candidate if best_candidate is not None else unique_candidates[0]
    # Normalização de oitavas musicais
    if final_bpm < 65.0:
        final_bpm *= 2.0
    elif final_bpm > 210.0:
        final_bpm /= 2.0

    final_bpm = round(float(final_bpm), 2)
    best_anchor = round(float(best_anchor), 4)

    return {
        "bpm": final_bpm,
        "anchor": best_anchor,
        "beat_times": best_beat_times,
    }
