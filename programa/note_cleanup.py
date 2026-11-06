"""Seleciona notas já detectadas: não transpõe e não força uma tonalidade.

O baixo assume um instrumento solo; a guitarra admite acordes de até seis notas.
Essas hipóteses podem falhar em baixos dobrados e vários violões no mesmo stem.
"""

from __future__ import annotations
import copy
from bisect import bisect_left, bisect_right
from pathlib import Path
import numpy as np
import librosa


def spectral_support(wav, notes):
    """Prominência local da frequência de cada nota, sem comparar volumes globais.

    É uma evidência auxiliar, não uma probabilidade de acerto. Harmônicos também
    podem produzir picos. O processamento trabalha em mono e usa somas prefixadas.
    """
    y, sr = librosa.load(str(wav), sr=11025, mono=True)
    if not len(y):
        return np.zeros(len(notes))
    hop, fft = 256, 4096
    spectrum = np.abs(librosa.stft(y, n_fft=fft, hop_length=hop))
    rms = librosa.feature.rms(y=y, frame_length=fft, hop_length=hop)[0]
    silence_floor = max(1e-6, float(np.quantile(rms, 0.95)) * 0.005)
    rms_prefix = np.r_[0, np.cumsum(rms, dtype=np.float64)]
    features = {}
    for pitch in {int(n.pitch) for n in notes}:
        center = round((440 * 2 ** ((pitch - 69) / 12)) * fft / sr)
        tolerance = max(1, round(center * 0.018))
        low, high = max(1, center - tolerance), min(len(spectrum), center + tolerance + 1)
        if low >= high:
            continue
        peak = spectrum[low:high].max(axis=0)
        lo, hi = max(1, low - 5), min(len(spectrum), high + 5)
        floor = spectrum[lo:hi].mean(axis=0)
        evidence = np.clip(peak / np.maximum(floor, 1e-8), 0, 5)
        features[pitch] = np.r_[0, np.cumsum(evidence, dtype=np.float64)]
    support = np.ones(len(notes))
    for i, note in enumerate(notes):
        prefix = features.get(int(note.pitch))
        if prefix is None:
            continue
        start = min(len(prefix) - 2, max(0, int(note.start * sr / hop)))
        stop = min(len(prefix) - 1, max(start + 1, int(np.ceil(note.end * sr / hop))))
        if (rms_prefix[stop] - rms_prefix[start]) / (stop - start) <= silence_floor:
            support[i] = 0
        else:
            support[i] = np.clip((prefix[stop] - prefix[start]) / (stop - start), 0.15, 4)
    return support


def max_polyphony(notes):
    active, maximum = 0, 0
    for _, change in sorted(
        [(float(n.start), 1) for n in notes] + [(float(n.end), -1) for n in notes]
    ):
        active += change
        maximum = max(maximum, active)
    return maximum


def clean_notes(midi, stem, *, wav=None, mode="auto"):
    """Mantém ataques originais, reduz sobreposições e evita gerar novas notas.

    Notas recusadas no ataque não reaparecem depois como falsas rearticulações.
    Não modifica voz, bateria, piano nem Outros.
    """
    if mode not in ("auto", "gentle", "off"):
        raise ValueError("Limpeza de notas inválida.")
    result = copy.deepcopy(midi)
    before = [n for inst in result.instruments for n in inst.notes]
    report = {
        "stem": stem,
        "mode": mode,
        "before": len(before),
        "after": len(before),
        "max_polyphony_before": max_polyphony(before),
        "max_polyphony_after": max_polyphony(before),
        "removed": 0,
        "shortened": 0,
    }
    if mode == "off" or stem not in ("bass", "guitar") or not before:
        return result, report
    limit = (1 if stem == "bass" else 6) if mode == "auto" else (2 if stem == "bass" else 8)
    notes = sorted(before, key=lambda n: (n.start, n.pitch, n.end))
    times = [n.start for n in notes]
    audio = spectral_support(wav, notes) if wav is not None else np.ones(len(notes))
    scores = np.zeros(len(notes))
    accepted = set()
    for i, n in enumerate(notes):
        lo, hi = bisect_left(times, n.start - 1.5), bisect_right(times, n.start + 1.5)
        local = float(np.quantile([x.velocity for x in notes[lo:hi]], 0.80))
        threshold = max(8, local * (0.65 if mode == "auto" else 0.40))
        duration = float(n.end - n.start)
        if n.velocity < threshold or duration < 0.035 or audio[i] <= 0:
            continue
        if duration < 0.075 and n.velocity < local * (0.85 if mode == "auto" else 0.60):
            continue
        # A penalização de notas curtas é gradual: ataques curtos fortes sobrevivem.
        scores[i] = (n.velocity / 127) * np.sqrt(min(duration, 0.30) / 0.08) * np.sqrt(audio[i])
        accepted.add(i)
    if stem == "bass":
        for i in list(accepted):
            high = notes[i]
            lo, hi = bisect_left(times, high.start - 0.035), bisect_right(times, high.start + 0.035)
            for j in range(lo, hi):
                low = notes[j]
                if (
                    j in accepted
                    and int(high.pitch - low.pitch) in (12, 19, 24)
                    and low.velocity >= high.velocity * 0.55
                    and audio[j] >= audio[i] * 0.55
                    and low.end >= high.start + min(0.08, (high.end - high.start) * 0.6)
                ):
                    scores[i] *= 0.50 if mode == "auto" else 0.75
                    break
    events = {}
    for i in accepted:
        n = notes[i]
        events.setdefault(float(n.start), [[], []])[0].append(i)
        events.setdefault(float(n.end), [[], []])[1].append(i)
    boundaries = sorted(events)
    active, previous, started, kept = set(), set(), {}, []
    # Seleção por evento: notas que perderam lugar não reiniciam no meio do sustain.
    for time in boundaries:
        new, ending = events[time]
        for i in ending:
            active.discard(i)
        active.update(new)
        candidates = active & (previous | set(new))
        ranked = sorted(
            candidates, key=lambda i: scores[i] * (1.12 if i in previous else 1), reverse=True
        )
        selected = set()
        pitches = set()
        for i in ranked:
            pitch = int(notes[i].pitch)
            if pitch not in pitches:
                selected.add(i)
                pitches.add(pitch)
            if len(selected) >= limit:
                break
        for i in previous - selected:
            start = started.pop(i)
            if time - start >= 0.035:
                n = copy.copy(notes[i])
                n.start = float(start)
                n.end = float(time)
                kept.append(n)
        for i in selected - previous:
            started[i] = time
        previous = selected
    report["after"] = len(kept)
    report["removed"] = len(notes) - len(kept)
    report["shortened"] = sum(
        1
        for n in kept
        if not any(
            abs(n.start - old.start) < 1e-8 and abs(n.end - old.end) < 1e-8 and n.pitch == old.pitch
            for old in notes[
                bisect_left(times, n.start - 1e-8) : bisect_right(times, n.start + 1e-8)
            ]
        )
    )
    report["max_polyphony_after"] = max_polyphony(kept)
    report["polyphony_limit"] = limit
    # Basic Pitch normalmente gera um instrumento; suporta mais sem multiplicar o limite.
    for inst in result.instruments:
        inst.notes = []
    result.instruments[0].notes = sorted(kept, key=lambda n: (n.start, n.pitch))
    return result, report


# Basic Pitch confunde parciais de uma nota forte com notas novas: oitava, oitava + quinta
# e duas oitavas acima. Essas notas "fantasma" aparecem junto do ataque da fundamental.
OVERTONE_INTERVALS = (12, 19, 24)


def remove_overtone_ghosts(notes, *, window=0.055, weaker=0.45, min_overlap=0.45):
    """Remove nota fraca que coincide com um harmônico de outra nota bem mais forte.

    Só descarta quando as três condições valem ao mesmo tempo: a nota começa quase junto
    da fundamental (até `window` s), é no máximo `weaker` vezes tão forte e soa em pelo
    menos `min_overlap` da sua duração durante a fundamental. Oitavas tocadas de verdade
    costumam ter intensidade parecida e permanecem. Retorna (notas mantidas, removidas).
    """
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    starts = [n.start for n in ordered]
    kept, removed = [], 0
    for note in ordered:
        duration = max(note.end - note.start, 1e-6)
        ghost = False
        low = bisect_left(starts, note.start - window)
        high = bisect_right(starts, note.start + window)
        for other in ordered[low:high]:
            if other is note or note.pitch - other.pitch not in OVERTONE_INTERVALS:
                continue
            if note.velocity > other.velocity * weaker:
                continue
            overlap = min(note.end, other.end) - max(note.start, other.start)
            if overlap >= duration * min_overlap:
                ghost = True
                break
        if ghost:
            removed += 1
        else:
            kept.append(note)
    return kept, removed
