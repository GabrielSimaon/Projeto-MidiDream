"""Filtro conservador de periodicidade. Não identifica pessoas nem garante separar coros."""

from __future__ import annotations
import copy
import math
import re
import numpy as np
from scipy.ndimage import binary_closing, maximum_filter1d


def parse_crowd_ranges(text: str) -> list[tuple[float, float]]:
    """Intervalos relativos ao início da música: mm:ss-mm:ss ou segundos-segundos."""

    def seconds(value):
        fields = value.strip().split(":")
        if len(fields) not in (1, 2, 3):
            raise ValueError("Tempo inválido: " + value)
        numbers = [float(x) for x in fields]
        if any(not math.isfinite(x) or x < 0 for x in numbers):
            raise ValueError("Use tempos positivos e finitos.")
        if len(numbers) > 1 and any(x >= 60 for x in numbers[1:]):
            raise ValueError("Minutos/segundos após dois-pontos devem ser menores que 60.")
        return sum(x * 60**i for i, x in enumerate(reversed(numbers)))

    intervals = []
    for line in re.split(r"[;\n]+", text):
        if not line.strip():
            continue
        pair = re.split(r"\s*[-–]\s*", line.strip())
        if len(pair) != 2:
            raise ValueError("Use um intervalo por linha: 01:20-01:28.")
        start, end = map(seconds, pair)
        if end <= start:
            raise ValueError("O final do trecho deve ser maior que o início.")
        intervals.append((start, end))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def periodic_support(y, sr, pitches, threshold=0.62, hop_seconds=0.02):
    """Autocorrelação normalizada por energia, em blocos limitados de memória.

    Verifica periodicidade no período da nota; não é uma probabilidade de ser o cantor.
    O resultado pode aceitar plateia afinada em uníssono e rejeitar voz rouca.
    """
    hop, frame = max(1, round(sr * hop_seconds)), 2048
    count = int(np.ceil(len(y) / hop))
    support = {p: np.zeros(count, dtype=bool) for p in pitches}
    if not count:
        return support, hop / sr
    padded = np.pad(np.asarray(y, dtype=np.float32), (frame // 2, frame // 2))
    windows = np.lib.stride_tricks.sliding_window_view(padded, frame)[::hop][:count]
    for begin in range(0, count, 256):
        block = windows[begin : begin + 256].astype(np.float64)
        block -= block.mean(axis=1, keepdims=True)
        energy = np.cumsum(block * block, axis=1)
        energy = np.pad(energy, ((0, 0), (1, 0)))
        spectrum = np.fft.rfft(block, n=frame * 2, axis=1)
        corr = np.fft.irfft(spectrum * spectrum.conj(), n=frame * 2, axis=1)[:, :frame]
        rms = np.sqrt(energy[:, -1] / frame)
        for pitch in pitches:
            period = sr / (440 * 2 ** ((pitch - 69) / 12))
            # Tolerância pequena ao vibrato, mantendo o teste ligado à nota prevista.
            lags = np.arange(
                max(2, round(period * 0.97)), min(frame // 2, round(period * 1.03)) + 1
            )
            if not len(lags):
                continue
            denom = np.sqrt(energy[:, frame - lags] * (energy[:, -1, None] - energy[:, lags]))
            score = np.max(corr[:, lags] / np.maximum(denom, 1e-12), axis=1)
            support[pitch][begin : begin + len(block)] = (score >= threshold) & (rms > 1e-5)
    return support, hop / sr


def filter_vocal_notes(midi, y, sr, *, automatic=True, threshold=0.62, excluded=()):
    """Recorta só notas vocais; preserva instrumentação e tempos fora dos cortes."""
    result = copy.deepcopy(midi)
    pitches = {n.pitch for inst in result.instruments for n in inst.notes}
    support, step = periodic_support(y, sr, pitches, threshold) if automatic else ({}, 0.02)
    for pitch, mask in support.items():
        mask = maximum_filter1d(mask.astype(np.uint8), size=3).astype(bool)
        support[pitch] = binary_closing(np.pad(mask, (3, 3)), structure=np.ones(5))[3:-3]
    report = {
        "method": "periodicidade; não identifica plateia com certeza",
        "automatic": automatic,
        "threshold": threshold,
        "manual_ranges": list(excluded),
        "notes_before": 0,
        "notes_after": 0,
        "rejected_or_trimmed": [],
    }
    for inst in result.instruments:
        retained = []
        for note in inst.notes:
            report["notes_before"] += 1
            segments = [(note.start, note.end)]
            if automatic:
                mask = support[note.pitch]
                lo, hi = (
                    max(0, int(np.floor(note.start / step))),
                    min(len(mask), int(np.ceil(note.end / step))),
                )
                local = mask[lo:hi]
                edges = np.diff(np.r_[False, local, False].astype(int))
                segments = [
                    (max(note.start, (lo + a) * step), min(note.end, (lo + b) * step))
                    for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))
                ]
            for cut_start, cut_end in excluded:
                pieces = []
                for start, end in segments:
                    if cut_end <= start or cut_start >= end:
                        pieces.append((start, end))
                    else:
                        if start < cut_start:
                            pieces.append((start, cut_start))
                        if end > cut_end:
                            pieces.append((cut_end, end))
                segments = pieces
            segments = [
                (a, b) for a, b in segments if b - a + 1e-9 >= min(0.06, note.end - note.start)
            ]
            if (
                len(segments) != 1
                or not segments
                or abs(segments[0][0] - note.start) > 0.001
                or abs(segments[0][1] - note.end) > 0.001
            ):
                report["rejected_or_trimmed"].append(
                    {
                        "start": round(float(note.start), 3),
                        "end": round(float(note.end), 3),
                        "pitch": int(note.pitch),
                        "kept": [[round(float(a), 3), round(float(b), 3)] for a, b in segments],
                    }
                )
            for start, end in segments:
                new = copy.copy(note)
                new.start, new.end = start, end
                retained.append(new)
        inst.notes = sorted(retained, key=lambda n: (n.start, n.pitch))
        report["notes_after"] += len(retained)
    return result, report
