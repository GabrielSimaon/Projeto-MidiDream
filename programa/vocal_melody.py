"""Melodia principal monofônica por pYIN, sem quantizar o canto."""

import numpy as np
import librosa
import pretty_midi
from scipy.ndimage import median_filter


def notes_from_f0(f0, voiced, probability, rms, sr, hop, duration):
    """Transforma a curva de frequência da voz em notas MIDI.

    f0: frequência em Hz por quadro; voiced: indica voz detectada.
    probability: confiança do detector; rms: energia do áudio.
    sr: amostras por segundo; hop: amostras entre quadros.
    duration: duração máxima em segundos. Retorna uma lista de notas."""
    count = min(len(f0), len(voiced), len(probability), len(rms))
    if not count:
        return []
    f0 = np.asarray(f0[:count])
    probability = np.asarray(probability[:count])
    peak = max(float(np.quantile(rms, 0.95)), 1e-8)
    # Exclui quadros sem voz, estimativas inválidas e energia muito baixa.
    valid = (
        np.asarray(voiced[:count], bool)
        & np.isfinite(f0)
        & (probability >= 0.18)
        & (rms[:count] > max(1e-4, peak * 0.055))
    )
    # Converte Hz para alturas MIDI; a mediana reduz oscilações curtas de afinação.
    pitch = librosa.hz_to_midi(np.where(np.isfinite(f0), f0, 440.0))
    pitch = np.rint(median_filter(pitch, size=11)).astype(int)
    pitch = np.where(valid, pitch, -1)
    # Fecha somente falhas muito curtas entre a mesma altura.
    # Cada mudança de altura delimita um possível começo/fim de nota.
    changes = np.r_[0, np.flatnonzero(pitch[1:] != pitch[:-1]) + 1, count]
    for a, b in zip(changes[:-1], changes[1:]):
        if (
            pitch[a] < 0
            and a > 0
            and b < count
            and (b - a) * hop / sr <= 0.045
            and pitch[a - 1] == pitch[b]
        ):
            pitch[a:b] = pitch[b]
    changes = np.r_[0, np.flatnonzero(pitch[1:] != pitch[:-1]) + 1, count]
    notes = []
    for a, b in zip(changes[:-1], changes[1:]):
        # a e b são índices de quadros; hop / sr converte cada quadro para segundos.
        start = a * hop / sr
        end = min(duration, b * hop / sr)
        if not 40 <= pitch[a] <= 88 or end - start < 0.08:
            continue
        velocity = int(np.clip(68 * np.sqrt(float(np.mean(rms[a:b])) / peak), 32, 88))
        notes.append(pretty_midi.Note(velocity, int(pitch[a]), start, end))
    return notes


def transcribe_vocal_melody(wav):
    """Lê a voz isolada e produz uma faixa de flauta, com uma nota por vez."""
    # Mono a 16 kHz reduz custo; hop=160 representa intervalos de 10 ms.
    y, sr = librosa.load(str(wav), sr=16000, mono=True)
    hop = 160
    frame = 1024
    midi = pretty_midi.PrettyMIDI(resolution=960)
    inst = pretty_midi.Instrument(73, name="Vocal Flute")
    midi.instruments.append(inst)
    if not len(y) or np.max(np.abs(y)) < 1e-6:
        return midi
    # pYIN estima a frequência fundamental da voz, em vez de vários acordes juntos.
    f0, voiced, prob = librosa.pyin(
        y,
        sr=sr,
        fmin=75,
        fmax=900,
        frame_length=frame,
        hop_length=hop,
        n_thresholds=32,
        resolution=0.2,
        fill_na=np.nan,
    )
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    inst.notes = notes_from_f0(f0, voiced, prob, rms, sr, hop, len(y) / sr)
    return midi
