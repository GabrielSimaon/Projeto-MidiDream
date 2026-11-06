#!/usr/bin/env python3
"""Converte stems de áudio em MIDI usando Basic Pitch e detecção espectral de bateria.

Uso:
  python audio2midi_corrigido.py "C:\\caminho\\da\\pasta_das_faixas" -o saida

Os arquivos de áudio podem estar dentro de subpastas. A pasta de saída é ignorada
na busca para evitar que WAVs temporários sejam processados na próxima execução.
"""

import os
from pathlib import Path

os.environ["ORT_DISABLE_TELEMETRY"] = "1"

# Garante acesso aos binários do FFmpeg caso não estejam no PATH global do sistema
tools_candidates = [
    os.environ.get("MIDI_STUDIO_TOOLS", ""),
    str(Path(__file__).resolve().parent / "ferramentas"),
    r"C:\Users\Desktop\AppData\Local\YouTubeMidiStudio\ferramentas",
]
for t_path in tools_candidates:
    if t_path and os.path.isdir(t_path) and t_path not in os.environ.get("PATH", ""):
        os.environ["PATH"] = t_path + os.pathsep + os.environ.get("PATH", "")

import argparse
import copy
import json
from live_vocals import parse_crowd_ranges, filter_vocal_notes
from note_cleanup import clean_notes, max_polyphony, remove_overtone_ghosts
from bass_pitch import validate_bass_pitch
from instrument_catalog import EXTRA, extra_kind
from musical_style import apply_style, DEFAULT_STYLE, style_drums
from rhythm_lock import lock_rhythm
from drum_timing import quantize_drums, split_hihat
from drum_classifier import classify_body, TOM_NOTES
from vocal_melody import transcribe_vocal_melody
import math
from functools import lru_cache
import re
import subprocess
from pathlib import Path

import librosa
import numpy as np
import pretty_midi
import scipy.ndimage

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".webm"}

GM_PROGRAMS = {
    "vocals": (52, "Choir Aahs"),
    "bass": (33, "Bass"),
    "guitar": (25, "Guitar"),
    "piano": (0, "Piano"),
    "other": (0, "Other - Acompanhamento nao identificado"),
}

GM_PROGRAMS.update({kind: (program, name) for kind, (program, name, _) in EXTRA.items()})

# Limites do detector por instrumento: frequências em Hz e duração mínima em ms.
STEM_PARAMS = {
    "vocals": {
        "onset": 0.25,
        "frame": 0.15,
        "min_len": 40.0,
        "fmin": 85.0,
        "fmax": 1200.0,
        "pmin": 43,
        "pmax": 84,
        "min_vel": 10,
    },
    "bass": {
        "onset": 0.38,
        "frame": 0.22,
        "min_len": 40.0,
        "fmin": 28.0,
        "fmax": 400.0,
        "pmin": 24,
        "pmax": 58,
        "min_vel": 12,
    },
    "guitar": {
        "onset": 0.38,
        "frame": 0.22,
        "min_len": 40.0,
        "fmin": 70.0,
        "fmax": 3000.0,
        "pmin": 40,
        "pmax": 88,
        "min_vel": 10,
    },
    "piano": {
        "onset": 0.42,
        "frame": 0.25,
        "min_len": 40.0,
        "fmin": 27.5,
        "fmax": 4186.0,
        "pmin": 21,
        "pmax": 108,
        "min_vel": 10,
    },
    "other": {
        "onset": 0.40,
        "frame": 0.24,
        "min_len": 40.0,
        "fmin": 80.0,
        "fmax": 2200.0,
        "pmin": 41,
        "pmax": 86,
        "min_vel": 10,
    },
}

STEM_PARAMS["acoustic_guitar"] = dict(STEM_PARAMS["guitar"])
STEM_PARAMS["electric_guitar"] = dict(STEM_PARAMS["guitar"])
STEM_PARAMS["accordion"] = {**STEM_PARAMS["other"], "fmin": 100.0, "fmax": 4000.0, "pmin": 43, "pmax": 96, "min_len": 40.0}

# Instrumentos em que o Basic Pitch costuma criar notas nos harmônicos de uma nota real.
POLYPHONIC_STEMS = {"piano", "guitar", "acoustic_guitar", "electric_guitar", "accordion", "other"}

# Não forçamos a escala de Si maior: a aplicação deve preservar a tonalidade
# e as notas detectadas da música original.
SCALE_CLASSES: set[int] = set()


def is_relative_to(path: Path, parent: Path) -> bool:
    """Verifica se um caminho está dentro de uma pasta."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def scan_audio_files(src_path: Path, output_path: Path) -> list[Path]:
    """Localiza arquivos de áudio, ignorando a pasta de saída."""
    if src_path.is_file():
        if src_path.suffix.lower() not in AUDIO_EXTS:
            raise ValueError(f"O arquivo informado não é um áudio compatível: {src_path}")
        return [src_path]
    if not src_path.is_dir():
        raise FileNotFoundError(f"A pasta de entrada não existe: {src_path}")

    found = []
    for p in src_path.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
            continue
        if is_relative_to(p, output_path):
            continue
        found.append(p)
    return sorted(found)


def classify_stem(path: Path) -> str:
    """Descobre se o áudio é baixo, bateria, voz ou acompanhamento pelo nome."""
    extra = extra_kind(path.stem)
    if extra:
        return extra
    low = path.stem.lower()
    if any(k in low for k in ("metro", "click", "metronome")):
        return "metronome"
    if any(k in low for k in ("drum", "bateria", "percuss")):
        return "drums"
    if any(k in low for k in ("bass", "baixo")):
        return "bass"
    if any(k in low for k in ("vocal", "voz", "voice")):
        return "vocals"
    if any(k in low for k in ("guitar", "violao", "violão", "guitarra")):
        return "guitar"
    if any(k in low for k in ("piano", "keys", "teclado")):
        return "piano"
    return "other"


def prepare_wav(src: Path, workdir: Path) -> Path:
    """Converte o áudio para WAV mono, no formato esperado pelos detectores."""
    workdir.mkdir(parents=True, exist_ok=True)
    # Prefixo com nome do arquivo mantém os stems separados na cache.
    dst = workdir / f"{src.stem}_norm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ar", "22050", "-ac", "1", str(dst)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return dst


def parse_bpm_hint(paths: list[Path]) -> float | None:
    """Procura uma indicação de BPM no nome dos arquivos."""
    for p in paths:
        match = re.search(r"(\d+(?:\.\d+)?)\s*bpm", p.stem.lower())
        if match:
            bpm = float(match.group(1))
            if 30.0 <= bpm <= 300.0:
                return bpm
    return None


def build_grid(
    max_duration: float, bpm: float, sub_div: int = 4, anchor: float = 0.0
) -> np.ndarray:
    """Cria a grade inteira até o fim dos stems, não só até o fim do metrônomo."""
    bpm = bpm if bpm > 0 else 158.0
    step = (60.0 / bpm) / max(1, sub_div)
    first_idx = math.floor((0.0 - anchor) / step)
    last_idx = math.ceil((max_duration - anchor) / step)
    indexes = np.arange(first_idx, last_idx + 1, dtype=np.float64)
    grid = anchor + indexes * step
    return grid[(grid >= 0.0) & (grid <= max_duration + step)]


ONSET_ENVELOPE_LATENCY = 0.017
MIN_PULSE_SCORE = 0.12


def estimate_beat_grid(
    reference_path: Path,
    bpm_hint: float,
    *,
    fast: bool = False,
    tolerance: float = 0.04,
) -> tuple[float, float] | None:
    """Mede o período e a fase reais do pulso perto do BPM informado.

    O BPM digitado costuma ser arredondado e a música quase nunca começa no segundo 0.
    Sem isso, uma grade de semicolcheias criada só com o BPM se desloca ao longo da
    música e os ataques da bateria caem na subdivisão errada. Procura o período dentro
    de ±`tolerance` do BPM que mais concentra ataques numa mesma fase. Retorna
    (bpm medido, fase do pulso em segundos) ou None se o áudio não tiver pulso claro.
    """
    sr, hop = 22050, 256
    try:
        y, _ = librosa.load(
            str(reference_path), sr=sr, mono=True, duration=60.0 if fast else None
        )
        if len(y) < sr * 8:
            return None
        envelope = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    except Exception as exc:
        print(f"[aviso] Não consegui medir o pulso do áudio: {exc}")
        return None
    envelope = np.maximum(envelope - np.median(envelope), 0.0)
    if envelope.sum() <= 1e-9:
        return None
    frames = np.arange(len(envelope)) * hop / sr
    bins = 96
    best = None
    for ratio in np.linspace(1 - tolerance, 1 + tolerance, 161):
        period = 60.0 / (bpm_hint * ratio)
        phase_bin = ((frames % period) / period * bins).astype(int) % bins
        histogram = np.bincount(phase_bin, weights=envelope, minlength=bins)
        # Janela de 3 compartimentos: tolera a largura do ataque.
        window = histogram + np.roll(histogram, 1) + np.roll(histogram, -1)
        index = int(window.argmax())
        score = float(window[index] / histogram.sum())
        if best is None or score > best[0]:
            best = (score, period, index)
    score, period, index = best
    # Sem pulso, a fase fica espalhada: ruído e cliques aleatórios medem 0.04–0.08 por janela;
    # pulsos reais (mesmo esparsos) medem 0.17–0.45. O limite fica no meio dessa folga.
    if score < MIN_PULSE_SCORE:
        return None
    # O fluxo espectral marca o ataque ~17 ms depois dele (janela da STFT); medido com cliques.
    phase = ((index + 0.5) / bins * period - ONSET_ENVELOPE_LATENCY) % period
    return 60.0 / period, float(phase)


def analyze_metronome(
    metro_path: Path | None,
    sub_div: int,
    max_duration: float,
    bpm_hint: float | None = None,
    reference_path: Path | None = None,
    fast: bool = False,
) -> tuple[float, np.ndarray]:
    """Usa o BPM informado ou estima pulsos do áudio e monta a grade rítmica."""
    bpm_detected = None
    anchor = 0.0
    if metro_path is not None:
        y, sr = librosa.load(str(metro_path), sr=44100, mono=True)
        hop = 256
        rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
        flux = np.maximum(0.0, np.diff(rms, prepend=0.0))
        if flux.size and flux.max() > 1e-6:
            flux /= flux.max()
            min_dist = max(1, int((0.15 * sr) / hop))
            local_max = scipy.ndimage.maximum_filter1d(flux, size=min_dist * 2 + 1)
            peaks = np.where((flux == local_max) & (flux >= 0.15))[0]
            clean_peaks = []
            for peak in peaks:
                if not clean_peaks or (peak - clean_peaks[-1]) >= min_dist:
                    clean_peaks.append(int(peak))
            click_times = librosa.frames_to_time(np.asarray(clean_peaks), sr=sr, hop_length=hop)
            if len(click_times) >= 2:
                median_ioi = float(np.median(np.diff(click_times)))
                if median_ioi > 0:
                    bpm_detected = 60.0 / median_ioi
                    anchor = float(click_times[0])

    # Se não houver BPM no nome nem metrônomo, estime-o a partir do áudio com máxima precisão.
    bpm_audio = None
    if bpm_hint is None and bpm_detected is None and reference_path is not None:
        if Path(reference_path).is_file():
            try:
                from bpm_detector import detect_precise_bpm
                bpm_info = detect_precise_bpm(reference_path)
                if bpm_info.get("bpm") and bpm_info.get("bpm") != 120.0:
                    bpm_audio = float(bpm_info["bpm"])
                    anchor = float(bpm_info["anchor"])
            except Exception:
                pass
        if bpm_audio is None:
            try:
                y, sr = librosa.load(str(reference_path), sr=22050, mono=True, duration=60.0 if fast else None)
                tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=512)
                if len(beat_frames) >= 2:
                    beat_indices = np.arange(len(beat_frames))
                    slope, intercept = np.polyfit(beat_indices, beat_frames * 512 / sr, 1)
                    if slope > 0:
                        bpm_audio = 60.0 / float(slope)
                        anchor = float(intercept)
                if bpm_audio is None and tempo is not None:
                    bpm_audio = float(tempo.item()) if hasattr(tempo, "item") else float(tempo)
            except Exception as exc:
                print(f"[aviso] A estimativa de BPM pelo áudio falhou: {exc}")
    bpm = (
        bpm_hint
        if bpm_hint is not None
        else (bpm_detected if bpm_detected else (bpm_audio if bpm_audio else 120.0))
    )
    # O MIDI mantém o BPM informado, mas a grade segue o pulso real do áudio.
    grid_bpm = bpm
    if bpm_hint is not None and metro_path is None and reference_path is not None:
        measured = estimate_beat_grid(reference_path, float(bpm_hint), fast=fast)
        if measured is not None:
            grid_bpm, phase = measured
            anchor = float(phase % (60.0 / grid_bpm / max(1, sub_div)))
            print(
                f"[tempo] Pulso medido no áudio: {grid_bpm:.2f} BPM (informado: {bpm:.2f}); "
                "a grade segue o áudio.",
                flush=True,
            )
        else:
            print("[aviso] Sem pulso claro no áudio; a grade usa o BPM informado.", flush=True)
    # No padrão MIDI, o compasso 1 tempo 1 começa em tick 0 (0.0s).
    # A âncora em 0.0 garante que as notas fiquem 100% no tempo dos players de MIDI/Karaokê.
    grid = build_grid(max_duration=max_duration, bpm=grid_bpm, sub_div=sub_div, anchor=0.0)
    if bpm_hint is not None:
        print(f"[tempo] BPM informado: {bpm:.2f}")
    elif bpm_detected is not None:
        print(f"[tempo] BPM estimado pelo metrônomo: {bpm:.2f}")
    elif bpm_audio is not None:
        print(f"[tempo] BPM estimado pelo áudio: {bpm:.2f}")
    else:
        print(f"[aviso] BPM não detectado; usando {bpm:.2f} BPM como aproximação.")
    print(f"[tempo] Grade gerada até {max_duration:.2f} segundos.")
    return bpm, grid


def snap_time_to_grid(t: float, grid_times: np.ndarray, step: float) -> float:
    """Move um instante em segundos para o ponto mais próximo da grade."""
    if grid_times.size:
        index = int(np.searchsorted(grid_times, t))
        if index == 0:
            return float(grid_times[0])
        if index == len(grid_times):
            return float(grid_times[-1])
        left, right = grid_times[index - 1], grid_times[index]
        return float(left if t - left <= right - t else right)
    return round(t / step) * step


def snap_to_scale(pitch: int) -> int:
    """Preserva a nota original; não há uma tonalidade fixa para todas as músicas."""
    return pitch


def rebuild_with_bpm(midi_in: pretty_midi.PrettyMIDI, bpm: float) -> pretty_midi.PrettyMIDI:
    """Copia notas em segundos para um novo MIDI com o andamento escolhido."""
    midi_out = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
    for inst in midi_in.instruments:
        new_inst = pretty_midi.Instrument(
            program=inst.program, is_drum=inst.is_drum, name=inst.name
        )
        for n in inst.notes:
            new_inst.notes.append(pretty_midi.Note(n.velocity, n.pitch, n.start, n.end))
        midi_out.instruments.append(new_inst)
    return midi_out


def post_process_notes(
    midi: pretty_midi.PrettyMIDI, stem: str, bpm: float, grid_times: np.ndarray, grid: int = 16
) -> pretty_midi.PrettyMIDI:
    """Filtra notas fora da faixa do instrumento e reúne sobreposições da mesma altura."""
    cfg = STEM_PARAMS.get(stem, STEM_PARAMS["other"])
    min_dur = max(cfg["min_len"] / 1000.0, 0.038)
    step = (60.0 / bpm) * (4.0 / grid) if grid else 0.0
    for inst in midi.instruments:
        if inst.is_drum:
            continue
        notes = [
            n
            for n in inst.notes
            if cfg["pmin"] <= n.pitch <= cfg["pmax"] and n.velocity >= cfg["min_vel"]
        ]
        if stem in POLYPHONIC_STEMS:
            # Piano e acordes: remove harmônicos falsos de oitava
            ghost_weaker = 0.40 if stem == "piano" else 0.45
            notes, ghosts = remove_overtone_ghosts(notes, weaker=ghost_weaker)
            if ghosts:
                print(f"  [harmônicos] {ghosts} notas fantasma de {stem} removidas", flush=True)
        for n in notes:
            n.pitch = snap_to_scale(n.pitch)
        notes.sort(key=lambda n: (n.pitch, n.start))
        merged = []
        for n in notes:
            if merged and merged[-1].pitch == n.pitch and n.start < merged[-1].end:
                if n.start > merged[-1].start + 0.02:
                    merged[-1].end = n.start
                    merged.append(n)
                else:
                    merged[-1].end = max(merged[-1].end, n.end)
                    merged[-1].velocity = max(merged[-1].velocity, n.velocity)
            else:
                merged.append(n)
        notes = [n for n in merged if (n.end - n.start) >= min_dur]
        for n in notes:
            q_start = snap_time_to_grid(n.start, grid_times, step) if grid else n.start
            q_end = snap_time_to_grid(n.end, grid_times, step) if grid else n.end
            n.start = max(0.0, q_start)
            n.end = max(n.start + 0.01, q_end)
        inst.notes = sorted(notes, key=lambda n: n.start)
    return midi


@lru_cache(maxsize=1)
def _pitch_model():
    """Carrega o modelo de notas uma única vez e reutiliza-o nas faixas seguintes."""
    try:
        import onnxruntime

        onnxruntime.disable_telemetry_events()
    except ImportError:
        pass
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import Model

    return Model(ICASSP_2022_MODEL_PATH)


def transcribe_stem(wav: Path, stem: str) -> pretty_midi.PrettyMIDI:
    """Detecta notas polifônicas do áudio com Basic Pitch."""
    try:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import predict
    except ImportError as exc:
        raise RuntimeError(
            "A biblioteca Basic Pitch não está instalada neste Python. "
            "Instale-a no mesmo ambiente em que executa o script: pip install basic-pitch"
        ) from exc

    cfg = STEM_PARAMS.get(stem, STEM_PARAMS["other"])
    _, midi, _ = predict(
        str(wav),
        _pitch_model(),
        onset_threshold=cfg["onset"],
        frame_threshold=cfg["frame"],
        minimum_note_length=cfg["min_len"],
        minimum_frequency=cfg["fmin"],
        maximum_frequency=cfg["fmax"],
        melodia_trick=False,
    )
    return midi


def transcribe_monophonic(wav: Path, stem: str) -> pretty_midi.PrettyMIDI:
    """Modo rápido: YIN para voz/baixo solo; não preserva coros ou acordes dessas pistas."""
    cfg = STEM_PARAMS[stem]
    y, sr = librosa.load(str(wav), sr=16000, mono=True)
    midi = pretty_midi.PrettyMIDI()
    instrument = pretty_midi.Instrument(GM_PROGRAMS[stem][0], name=GM_PROGRAMS[stem][1])
    midi.instruments.append(instrument)
    if not len(y) or float(np.max(np.abs(y))) < 1e-6:
        return midi
    hop, frame = 256, 2048
    f0 = librosa.yin(
        y, fmin=cfg["fmin"], fmax=cfg["fmax"], sr=sr, frame_length=frame, hop_length=hop
    )
    pitches = np.rint(librosa.hz_to_midi(f0)).astype(int)
    pitches = scipy.ndimage.median_filter(pitches, size=3)
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    flatness = librosa.feature.spectral_flatness(y=y, n_fft=frame, hop_length=hop)[0]
    count = min(len(pitches), len(rms), len(flatness))
    threshold = max(1e-4, float(np.quantile(rms, 0.95)) * 0.04)
    valid = (rms[:count] > threshold) & (flatness[:count] < 0.25)
    pitches = np.where(valid, pitches[:count], -1)
    changes = np.r_[0, np.flatnonzero(pitches[1:] != pitches[:-1]) + 1, count]
    peak = max(float(np.quantile(rms, 0.95)), 1e-6)
    duration = len(y) / sr
    for start, stop in zip(changes[:-1], changes[1:]):
        pitch = int(pitches[start])
        t0, t1 = start * hop / sr, min(stop * hop / sr, duration)
        if cfg["pmin"] <= pitch <= cfg["pmax"] and t1 - t0 >= 0.06:
            velocity = int(np.clip(95 * np.sqrt(float(np.mean(rms[start:stop])) / peak), 25, 110))
            instrument.notes.append(pretty_midi.Note(velocity, pitch, t0, t1))
    return midi


def transcribe_drums(
    wav: Path, bpm: float, grid_times: np.ndarray, grid: int = 16, fast: bool = False
) -> pretty_midi.PrettyMIDI:
    """Detecta bumbo, caixa, tons/surdo e pratos com fluxo espectral + relação entre bandas.

    O método anterior normalizava cada filtro separadamente, então um prato com
    pouco conteúdo na faixa da caixa podia disparar as duas notas. Aqui as notas
    só são aceitas quando a energia relativa da região correspondente também é
    forte. Ataques realmente simultâneos continuam podendo gerar mais de uma nota.
    """
    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    n_fft, hop = (1024, 256) if fast else (2048, 128)
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    if S.shape[1] == 0:
        midi = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
        midi.instruments.append(pretty_midi.Instrument(program=0, is_drum=True, name="Drums"))
        return midi

    # Silêncio não deve produzir centenas de ataques com limiar zero.
    if np.max(np.abs(y), initial=0.0) < 1e-6:
        midi = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
        midi.instruments.append(pretty_midi.Instrument(0, is_drum=True, name="Drums"))
        return midi

    flux_bands = {
        "kick": (30.0, 150.0),
        "tom": (80.0, 500.0),
        "snare": (150.0, 2500.0),
        "cym_low": (2500.0, 5500.0),
        "cym_high": (5500.0, 16000.0),
    }
    flux = {}
    for name, (lo, hi) in flux_bands.items():
        mask = (freqs >= lo) & (freqs < hi)
        band = S[mask]
        values = np.maximum(0.0, np.diff(band, axis=1, prepend=band[:, :1])).sum(axis=0)
        flux[name] = scipy.ndimage.gaussian_filter1d(values, sigma=1.0)

    cym_flux = flux["cym_low"] + flux["cym_high"]
    q95 = {name: max(float(np.quantile(values, 0.95)), 1e-9) for name, values in flux.items()}
    q95["cym"] = max(float(np.quantile(cym_flux, 0.95)), 1e-9)

    # Encontre candidatos em cada banda, com limiar relativo ao stem inteiro.
    candidate_indices = []
    peaks_by_band = {}
    for name, values in flux.items():
        threshold = max(1e-8, float(np.quantile(values, 0.75)) * 0.65, float(values.max()) * 0.012)
        is_peak = (values == scipy.ndimage.maximum_filter1d(values, size=(5 if fast else 11))) & (
            values >= threshold
        )
        min_gap_s = 0.045 if name.startswith("cym") else 0.065
        min_gap = max(1, int(min_gap_s * sr / hop))
        kept = []
        for idx in np.flatnonzero(is_peak):
            idx = int(idx)
            if not kept or idx - kept[-1] >= min_gap:
                kept.append(idx)
            elif values[idx] > values[kept[-1]]:
                kept[-1] = idx
        peaks_by_band[name] = kept
        candidate_indices.extend(kept)

    # Mescle candidatos que são o mesmo ataque, mantendo a posição de maior fluxo.
    candidate_indices = sorted(set(candidate_indices))
    clusters = []
    merge_gap = max(1, int(0.020 * sr / hop))
    for idx in candidate_indices:
        if not clusters or idx - clusters[-1][-1] > merge_gap:
            clusters.append([idx])
        else:
            clusters[-1].append(idx)
    candidates = [
        max(group, key=lambda i: sum(values[i] / q95[name] for name, values in flux.items()))
        for group in clusters
    ]

    def energy(mask: np.ndarray, start: int, stop: int) -> float:
        if not np.any(mask) or stop <= start:
            return 0.0
        return float(np.square(S[mask, start:stop]).sum())

    low_mask = (freqs >= 30.0) & (freqs < 150.0)
    body_mask = (freqs >= 150.0) & (freqs < 2500.0)
    cym_low_mask = (freqs >= 2500.0) & (freqs < 5500.0)
    cym_high_mask = (freqs >= 5500.0) & (freqs < 16000.0)

    midi = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
    drum_inst = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    t_per_frame = hop / sr
    n_frames = S.shape[1]

    for idx in candidates:
        # 80 ms de som após o ataque ajudam a distinguir o corpo da caixa do brilho do prato.
        start_frame = max(0, idx - 1)
        stop_frame = min(n_frames, idx + max(4, int(0.080 / t_per_frame)))
        e_low = energy(low_mask, start_frame, stop_frame)
        e_body = energy(body_mask, start_frame, stop_frame)
        e_cym_low = energy(cym_low_mask, start_frame, stop_frame)
        e_cym_high = energy(cym_high_mask, start_frame, stop_frame)
        total = e_low + e_body + e_cym_low + e_cym_high + 1e-12
        low_frac = e_low / total
        body_frac = e_body / total
        cym_frac = (e_cym_low + e_cym_high) / total

        kick_strength = float(flux["kick"][idx] / q95["kick"])
        snare_strength = float(flux["snare"][idx] / q95["snare"])
        cym_strength = float(cym_flux[idx] / q95["cym"])

        time = float(librosa.frames_to_time(idx, sr=sr, hop_length=hop))
        tom_pitch, sub_fraction, has_snare_noise = classify_body(y, sr, time)
        tom_strength = float(flux["tom"][idx] / q95["tom"])

        detected = []

        # 1. Prato de Ataque (Crash - MIDI 49): alta energia de banda larga com decaimento metálico sustentado
        early_stop = min(n_frames, idx + max(2, int(0.035 / t_per_frame)))
        late_stop = min(n_frames, idx + max(4, int(0.125 / t_per_frame)))
        early = energy(cym_low_mask, start_frame, early_stop)
        late = energy(cym_low_mask, early_stop, late_stop)
        early_high = energy(cym_high_mask, start_frame, early_stop)
        late_high = energy(cym_high_mask, early_stop, late_stop)

        is_crash = (
            cym_frac >= 0.18
            and (cym_strength >= 0.40 or float(flux["cym_low"][idx] / q95["cym_low"]) >= 0.45)
            and (late / (early + 1e-12) >= 0.15 or late_high / (early_high + 1e-12) >= 0.18)
            and not (body_frac >= 0.25 and snare_strength >= cym_strength * 1.05)
        )
        if is_crash:
            velocity = int(np.clip(84 + 28 * np.sqrt(cym_strength), 80, 122))
            detected.append((49, velocity, 0.35))

        # 2. Bumbo (Kick - MIDI 36): subgrave real dominante (< 58 Hz)
        is_kick = (
            low_frac >= 0.12
            and kick_strength >= 0.48
            and (sub_fraction >= 0.28 or tom_pitch is None)
            and not (cym_frac > 0.45 and cym_strength > kick_strength * 1.3)
        )
        if is_kick:
            velocity = int(np.clip(50 + 38 * np.sqrt(kick_strength), 45, 127))
            detected.append((36, velocity, 0.10))

        # 3. Caixa (Snare - MIDI 38): corpo médio + esteira acústica (exclusiva contra tom e bumbo)
        is_snare = (
            not is_kick
            and body_frac >= 0.14
            and snare_strength >= 0.44
            and (has_snare_noise or tom_pitch is None)
            and not (cym_frac > 0.45 and cym_strength > snare_strength * 1.25)
            and not is_crash
        )
        if is_snare:
            velocity = int(np.clip(45 + 36 * np.sqrt(snare_strength), 40, 122))
            detected.append((38, velocity, 0.10))

        # 4. Tons e Surdos (MIDI 41 a 50): tambores melódicos em viradas (não duplicam bumbo nem caixa)
        is_tom = (
            not is_kick
            and not is_snare
            and not is_crash
            and tom_pitch is not None
            and (tom_strength >= 0.35 or (body_frac >= 0.22 and not has_snare_noise))
        )
        if is_tom:
            velocity = int(np.clip(82 + 38 * np.sqrt(max(0.1, tom_strength)), 78, 127))
            detected.append((tom_pitch, velocity, 0.20))

        # 5. Prato de Condução (Ride - MIDI 51): condução com corpo metálico em cym_low sem esteira de caixa
        low_strength = float(flux["cym_low"][idx] / q95["cym_low"])
        is_ride = (
            not is_crash
            and not is_snare
            and not is_kick
            and low_strength >= 0.40
            and cym_frac >= 0.20
            and flux["cym_low"][idx] >= flux["cym_high"][idx] * 0.70
        )
        if is_ride:
            velocity = int(np.clip(72 + 25 * np.sqrt(low_strength), 68, 108))
            detected.append((51, velocity, 0.18))

        for pitch, velocity, duration in detected:
            drum_inst.notes.append(pretty_midi.Note(velocity, pitch, time, time + duration))

    # O chimbal direto: detectado nas altas frequências (fechado 42 ou aberto 46)
    for idx in peaks_by_band.get("cym_high", []):
        high_strength = float(flux["cym_high"][idx] / q95["cym_high"])
        early_stop = min(n_frames, idx + max(2, int(0.035 / t_per_frame)))
        late_stop = min(n_frames, idx + max(4, int(0.1 / t_per_frame)))
        first = energy(cym_high_mask, max(0, idx - 1), early_stop)
        tail = energy(cym_high_mask, early_stop, late_stop)
        first_density = first / max(1, early_stop - max(0, idx - 1))
        tail_density = tail / max(1, late_stop - early_stop)
        high_flux_share = float(flux["cym_high"][idx] / (cym_flux[idx] + 1e-12))
        if high_strength < 0.28 or first <= 1e-10:
            continue
        if high_flux_share < 0.35 and tail_density >= first_density * 0.85:
            continue
        time = float(librosa.frames_to_time(idx, sr=sr, hop_length=hop))

        # Chimbal aberto vs fechado: cauda de sustentação mais longa no prato aberto
        tail_ratio = tail_density / (first_density + 1e-12)
        if tail_ratio >= 0.35 and high_strength >= 0.42:
            hat_pitch = 46  # Open Hi-Hat
            hat_dur = 0.15
        else:
            hat_pitch = 42  # Closed Hi-Hat
            hat_dur = 0.055

        velocity = int(np.clip(68 + 20 * np.sqrt(high_strength), 55, 110))
        drum_inst.notes.append(pretty_midi.Note(velocity, hat_pitch, time, time + hat_dur))

    # ANÁLISE CONTEXTUAL DO GROOVE E INJEÇÃO DE CHIMBAL:
    # O chimbal deve soar quase todas as vezes que o bumbo e a caixa aparecem,
    # exceto em viradas (fills com tons ou rufos de caixa) ou ataques de pratos.
    current_notes = sorted(drum_inst.notes, key=lambda n: n.start)
    tom_times = [n.start for n in current_notes if n.pitch in TOM_NOTES]
    cymbal_times = [n.start for n in current_notes if n.pitch in (49, 51, 57)]
    hat_times = [n.start for n in current_notes if n.pitch in (42, 44, 46)]
    snare_times = [n.start for n in current_notes if n.pitch == 38]

    additional_hats = []
    for n in current_notes:
        if n.pitch in (36, 38):  # Bumbo ou Caixa
            t = n.start
            # 1. Silencia chimbal em viradas de bateria (tons ou surdos próximos)
            is_fill = any(abs(t - tom_t) < 0.25 for tom_t in tom_times)
            # 2. Silencia em rufos de caixa (mais de 2 caixas em menos de 0.35s)
            is_snare_roll = sum(1 for st in snare_times if abs(t - st) < 0.35) >= 3
            # 3. Silencia se houver ataque simultâneo de prato (Crash ou Ride)
            has_cymbal = any(abs(t - ct) < 0.05 for ct in cymbal_times)
            # 4. Verifica se já existe chimbal nesse ataque
            has_hat = any(abs(t - ht) < 0.045 for ht in hat_times)

            if not is_fill and not is_snare_roll and not has_cymbal and not has_hat:
                hat_vel = int(np.clip(n.velocity * 0.85, 70, 95))
                additional_hats.append(pretty_midi.Note(hat_vel, 42, t, t + 0.055))
                hat_times.append(t)

    drum_inst.notes.extend(additional_hats)

    suppressed = []
    last_by_pitch = {}
    for note in sorted(drum_inst.notes, key=lambda n: (n.start, n.pitch)):
        previous = last_by_pitch.get(note.pitch)
        gap = {36: 0.07, 38: 0.08, 41: 0.08, 42: 0.065, 43: 0.08, 45: 0.08, 46: 0.08, 47: 0.08, 48: 0.08, 49: 0.40, 50: 0.08, 51: 0.08}.get(note.pitch, 0.055)
        if previous is not None and note.start - previous.start < gap:
            if note.velocity > previous.velocity:
                suppressed.remove(previous)
            else:
                continue
        suppressed.append(note)
        last_by_pitch[note.pitch] = note
    drum_inst.notes = sorted(suppressed, key=lambda n: (n.start, n.pitch))
    midi.instruments.append(drum_inst)
    quantize_drums(midi, bpm, grid_times, grid)
    counts = {
        "kick": sum(1 for note in drum_inst.notes if note.pitch == 36),
        "snare": sum(1 for note in drum_inst.notes if note.pitch == 38),
        "toms": sum(1 for note in drum_inst.notes if note.pitch in TOM_NOTES),
        "hat": sum(1 for note in drum_inst.notes if note.pitch in (42, 44, 46)),
        "cymbals": sum(1 for note in drum_inst.notes if note.pitch in (49, 51, 57)),
    }
    print(
        "  [bateria] bumbo: {kick}; caixa: {snare}; tons/surdo: {toms}; chimbal: {hat}; pratos (crash/ride): {cymbals}".format(
            **counts
        )
    )
    return midi


def main() -> int:
    """Coordena a conversão dos stems e salva faixas individuais e o MIDI combinado."""
    ap = argparse.ArgumentParser(
        description="Converte stems de áudio em arquivos MIDI separados e combinados."
    )
    ap.add_argument(
        "--drum-grid",
        type=int,
        choices=(8, 16, 32),
        default=16,
        help="Grade própria da bateria; 16 = semicolcheias",
    )
    ap.add_argument("source", help="Pasta com os stems de áudio (ou um arquivo de áudio)")
    ap.add_argument("-o", "--output", default="saida", help="Pasta de saída (padrão: ./saida)")
    ap.add_argument(
        "--grid",
        type=int,
        choices=(0, 4, 8, 16, 32),
        default=0,
        help="Grade de quantização; 0 preserva os tempos detectados",
    )
    ap.add_argument(
        "--bpm", type=float, default=None, help="BPM manual para usar como referência (opcional)"
    )
    ap.add_argument("--reference", help="Mix original para detectar o BPM")
    ap.add_argument(
        "--quick-pitch", action="store_true", help="Estimativa YIN mais rápida para voz e baixo"
    )
    ap.add_argument(
        "--fast", action="store_true", help="Bateria e estimativa de andamento com análise reduzida"
    )
    ap.add_argument("--live-vocals", choices=("off", "balanced", "strict"), default="off")
    ap.add_argument("--crowd-ranges", default="")
    ap.add_argument(
        "--vocal-polyphonic",
        action="store_true",
        help="Preserva coros polifônicos em vez de extrair uma melodia",
    )
    ap.add_argument(
        "--no-rhythm-lock",
        action="store_true",
        help="Desativa baixo no bumbo e violão em colcheias",
    )
    ap.add_argument(
        "--note-cleanup", choices=("musical", "auto", "gentle", "off"), default="musical"
    )
    ap.add_argument("--style-profile")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()
    style_profile = (
        json.loads(Path(args.style_profile).read_text(encoding="utf-8"))
        if args.style_profile
        else DEFAULT_STYLE
    )
    excluded = parse_crowd_ranges(args.crowd_ranges)
    if args.bpm is not None and not (30.0 <= args.bpm <= 300.0):
        ap.error("--bpm deve estar entre 30 e 300")

    src_path = Path(args.source).expanduser().resolve()
    out = Path(args.output).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    wav_dir = out / "wav_cache"

    try:
        audio_files = scan_audio_files(src_path, out)
    except (ValueError, FileNotFoundError) as exc:
        print(f"[erro] {exc}")
        return 2
    if not audio_files:
        print(f"[erro] Nenhum áudio foi encontrado em: {src_path}")
        print("Coloque os MP3/WAV dentro da pasta indicada e execute o script novamente.")
        return 2

    stems: dict[str, Path] = {}
    metro: Path | None = None
    print(f"[entrada] Áudios encontrados: {len(audio_files)}")
    for p in audio_files:
        kind = classify_stem(p)
        if kind == "metronome":
            metro = prepare_wav(p, wav_dir)
            continue
        if kind in stems:
            raise ValueError(
                f"Dois arquivos classificados como {kind}: renomeie ou envie um por instrumento."
            )
        stems[kind] = prepare_wav(p, wav_dir)

    if not stems:
        print("[erro] Só encontrei metrônomo/click, nenhuma faixa musical para converter.")
        return 2

    if args.compact:
        accompaniment = [stems.pop(key) for key in ("guitar", "piano", "other") if key in stems]
        if len(accompaniment) == 1:
            stems["guitar"] = accompaniment[0]
        elif accompaniment:
            wav_dir.mkdir(parents=True, exist_ok=True)
            mixed = wav_dir / "acompanhamento_norm.wav"
            command = ["ffmpeg", "-y", "-nostdin"]
            for file in accompaniment:
                command.extend(["-i", str(file)])
            command.extend(
                [
                    "-filter_complex",
                    f"amix=inputs={len(accompaniment)}:duration=longest:normalize=1",
                    "-ar",
                    "22050",
                    "-ac",
                    "1",
                    str(mixed),
                ]
            )
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            stems["guitar"] = mixed

    # Descobre a duração real das faixas para a grade cobrir a música inteira.
    durations = []
    for path in stems.values():
        try:
            durations.append(float(librosa.get_duration(path=str(path))))
        except Exception:
            pass
    max_duration = max(durations) if durations else 0.0
    if max_duration <= 0:
        print("[erro] Não consegui ler a duração dos arquivos de áudio.")
        return 2

    bpm_hint = args.bpm if args.bpm is not None else parse_bpm_hint(audio_files)
    reference_path = (
        Path(args.reference)
        if args.reference
        else (stems.get("drums") or stems.get("bass") or next(iter(stems.values())))
    )
    bpm, grid_times = analyze_metronome(
        metro,
        sub_div=max(1, args.grid // 4),
        max_duration=max_duration,
        bpm_hint=bpm_hint,
        reference_path=reference_path,
        fast=args.fast,
    )
    if args.grid and style_profile.get("grid_anchor") is not None:
        grid_times = build_grid(
            max_duration,
            bpm,
            sub_div=max(1, args.grid // 4),
            anchor=float(style_profile["grid_anchor"]),
        )
    # A bateria mantém sua grade mesmo quando o usuário preserva os tempos vocais.
    drum_anchor = float(grid_times[0]) if len(grid_times) else 0.0
    # O período da bateria vem da grade medida, não do BPM digitado.
    grid_sub = max(1, args.grid // 4)
    grid_bpm = (
        60.0 / (float(np.median(np.diff(grid_times))) * grid_sub) if len(grid_times) > 1 else bpm
    )
    drum_grid_times = build_grid(
        max_duration, grid_bpm, sub_div=args.drum_grid // 4, anchor=drum_anchor
    )
    print("[faixas] " + ", ".join(f"{name} ({path.name})" for name, path in sorted(stems.items())))

    midi_dir = out / "midi"
    midi_dir.mkdir(parents=True, exist_ok=True)
    combined = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
    total_notes = 0
    cleanup_reports = []

    # Cada stem usa seu detector: transientes para bateria, pYIN para voz principal,
    # Basic Pitch para instrumentos que podem tocar várias notas simultâneas.
    for track_index, (name, path) in enumerate(sorted(stems.items())):
        print(f"[progress] {track_index}/{len(stems)} {name}", flush=True)
        print(f"[convertendo] {name}...")
        if name == "drums":
            midi = transcribe_drums(path, bpm, drum_grid_times, grid=args.drum_grid, fast=args.fast)
            split_hihat(midi)
            if args.note_cleanup == "musical":
                midi = style_drums(midi, style_profile)
            hats = [i for i in midi.instruments if i.name == "Drums - Chimbal"]
            if hats:
                single_hat = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
                single_hat.instruments = hats
                single_hat.write(str(midi_dir / "hihat.mid"))
        else:
            raw = (
                transcribe_vocal_melody(path)
                if name == "vocals" and not args.vocal_polyphonic
                else transcribe_monophonic(path, name)
                if args.quick_pitch and name in ("bass", "vocals")
                else transcribe_stem(path, name)
            )
            raw_count = sum(len(inst.notes) for inst in raw.instruments)
            bass_report = None
            if name == "bass" and args.note_cleanup != "off":
                print("  [baixo] Conferindo a fundamental no áudio...", flush=True)
                raw, bass_report = validate_bass_pitch(raw, path)
            midi = rebuild_with_bpm(raw, bpm)
            midi = post_process_notes(
                midi, name, bpm, grid_times, grid=args.grid
            )
            before_musical = copy.deepcopy(midi)
            if args.note_cleanup == "musical":
                midi = apply_style(midi, name, bpm, grid_times, args.grid, style_profile)
            if name == "vocals" and (args.live_vocals != "off" or excluded):
                print(
                    "  [ao vivo] Conferindo notas vocais; coro afinado pode continuar passando pelo filtro.",
                    flush=True,
                )
                y_vocal, sr_vocal = librosa.load(str(path), sr=16000, mono=True)
                midi, live_report = filter_vocal_notes(
                    midi,
                    y_vocal,
                    sr_vocal,
                    automatic=args.live_vocals != "off",
                    threshold=0.78 if args.live_vocals == "strict" else 0.62,
                    excluded=excluded,
                )
                try:
                    (midi_dir / "relatorio_voz.json").write_text(
                        json.dumps(live_report, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except (OSError, TypeError, ValueError) as exc:
                    print(
                        f"  [aviso] Não consegui salvar o relatório vocal: {exc}. A geração do MIDI continua.",
                        flush=True,
                    )
                print(
                    f"  [ao vivo] {len(live_report['rejected_or_trimmed'])} notas removidas ou recortadas; sem mudar o tempo da música.",
                    flush=True,
                )
            if name in ("bass", "guitar", "acoustic_guitar", "electric_guitar"):
                if args.note_cleanup != "off":
                    before_cleanup = out / "antes_limpeza"
                    before_cleanup.mkdir(exist_ok=True)
                    (before_musical if args.note_cleanup == "musical" else midi).write(
                        str(before_cleanup / f"{name}.mid")
                    )
                if args.note_cleanup == "musical" and name == "bass" and bass_report and bass_report["checked"]:
                    midi, cleanup_report = clean_notes(midi, name, wav=path, mode="auto")
                    cleanup_report["mode"] = "musical"
                elif args.note_cleanup == "musical":
                    cleanup_report = {
                        "stem": name,
                        "mode": "musical",
                        "before": raw_count,
                        "after": sum(len(i.notes) for i in midi.instruments),
                        "max_polyphony_before": max_polyphony(raw.instruments[0].notes)
                        if raw.instruments
                        else 0,
                        "max_polyphony_after": max_polyphony(
                            [n for i in midi.instruments for n in i.notes]
                        ),
                    }
                else:
                    midi, cleanup_report = clean_notes(midi, name, wav=path, mode=args.note_cleanup)
                if bass_report is not None:
                    cleanup_report["pitch_validation"] = bass_report
                cleanup_reports.append(cleanup_report)
                print(
                    f"  [limpeza] {name}: {cleanup_report['before']} -> {cleanup_report['after']} notas; simultâneas {cleanup_report['max_polyphony_before']} -> {cleanup_report['max_polyphony_after']}",
                    flush=True,
                )
            if name == "bass":
                from bass_pitch import sustain_bass_groove
                midi = sustain_bass_groove(midi, bpm=bpm)
            prog, label = GM_PROGRAMS.get(name, (0, name.title()))
            prog = int(style_profile.get("stats", {}).get(name, {}).get("program", prog))
            for inst in midi.instruments:
                inst.program, inst.name = prog, label
            final_count = sum(len(inst.notes) for inst in midi.instruments)
            print(f"  [notas] antes dos filtros: {raw_count}; depois dos filtros: {final_count}")

        out_file = midi_dir / f"{name}.mid"
        midi.write(str(out_file))
        count = sum(len(inst.notes) for inst in midi.instruments)
        total_notes += count
        end_time = max((n.end for inst in midi.instruments for n in inst.notes), default=0.0)
        print(f"  -> {out_file.name}: {count} notas, duração MIDI {end_time:.2f}s")
        if count == 0:
            print(
                f"  [aviso] {name}.mid ficou vazio: o detector não produziu notas ou os filtros removeram todas."
            )
        for inst in midi.instruments:
            combined.instruments.append(inst)
        print(f"[progress] {track_index + 1}/{len(stems)} {name} concluída", flush=True)

    # Limpeza de vazamento cruzado entre instrumentos (elimina instrumentos de fundo)
    try:
        from bleed_cleaner import clean_midi_bleed
        stems_midi_map = {}
        for s_name in stems:
            f = midi_dir / f"{s_name}.mid"
            if f.is_file():
                stems_midi_map[s_name] = pretty_midi.PrettyMIDI(str(f))
        if stems_midi_map:
            removed_bleed = clean_midi_bleed(stems_midi_map)
            total_bleed_removed = sum(removed_bleed.values())
            if total_bleed_removed > 0:
                print(f"  [vazamento] {total_bleed_removed} notas de fundo vazadas removidas entre faixas: {removed_bleed}", flush=True)
                for s_name, s_mid in stems_midi_map.items():
                    s_mid.write(str(midi_dir / f"{s_name}.mid"))
                combined.instruments = []
                for s_name in sorted(stems_midi_map):
                    for inst in stems_midi_map[s_name].instruments:
                        combined.instruments.append(inst)
                total_notes = sum(len(i.notes) for i in combined.instruments)
    except Exception as exc:
        print(f"  [aviso] Limpeza de vazamento: {exc}")

    # Depois de detectar as alturas, simplifica o arranjo usando bumbo e grade.
    if not args.no_rhythm_lock:
        rhythm_report = lock_rhythm(combined.instruments, bpm, grid_times)
        try:
            (midi_dir / "relatorio_ritmo.json").write_text(
                json.dumps(rhythm_report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, TypeError, ValueError) as exc:
            print(f"[aviso] Relatório rítmico indisponível: {exc}")
        for name in stems:
            file = midi_dir / f"{name}.mid"
            single = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=480)
            label = GM_PROGRAMS.get(name, (0, name.title()))[1]
            single.instruments = [
                i
                for i in combined.instruments
                if (i.is_drum if name == "drums" else i.name == label)
            ]
            if single.instruments:
                single.write(str(file))
        for report in cleanup_reports:
            label = GM_PROGRAMS.get(report["stem"], (0, report["stem"].title()))[1]
            final = [n for i in combined.instruments if i.name == label for n in i.notes]
            report["before_rhythm"] = report["after"]
            report["after"] = len(final)
            report["max_polyphony_after"] = max_polyphony(final)
        total_notes = sum(len(i.notes) for i in combined.instruments)
        print(f"[ritmo] Baixo no bumbo; violão em colcheias: {rhythm_report}", flush=True)

    try:
        (midi_dir / "relatorio_limpeza.json").write_text(
            json.dumps({"tracks": cleanup_reports}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError) as exc:
        print(f"[aviso] Relatório de limpeza não foi salvo: {exc}")
    combined_file = midi_dir / "completo.mid"
    combined.write(str(combined_file))
    combined_end = max((n.end for inst in combined.instruments for n in inst.notes), default=0.0)
    print(f"\n[ok] Arquivos salvos em: {midi_dir}")
    print(f"[resumo] Total de notas: {total_notes}; duração do MIDI completo: {combined_end:.2f}s")
    if total_notes == 0:
        print(
            "[problema] O MIDI saiu com 0 segundos porque nenhuma nota foi gerada. Confira as linhas '[notas]' acima."
        )
        print(
            "Se 'antes dos filtros' for 0, o problema está na transcrição/Basic Pitch; se só cair depois dos filtros, os limites de notas estão restritivos."
        )
        if args.live_vocals != "off" or excluded:
            print("[ao vivo] Filtro pode deixar a faixa vocal vazia; MIDI salvo para revisão.")
            return 0
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
