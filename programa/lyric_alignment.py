"""Alinha palavras ao áudio pelo Whisper; não usa a duração total como relógio."""

import os

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
import math

# Pausas vocais a partir deste tamanho (s) não recebem palavras.
NONSPEECH_SKIP_SECONDS = 4.0


def normalize_alignment(result, language):
    """Converte a saída do alinhador em palavras com início/fim em segundos.

    Evita que uma pausa instrumental seja contada como parte de uma palavra."""
    words = []
    previous = 0.0
    segments = result["segments"] if isinstance(result, dict) else result.to_dict()["segments"]
    for index, segment in enumerate(segments):
        for w in segment.get("words", []):
            text = w.get("word", "").strip()
            if not text:
                continue
            start = float(w["start"])
            end = float(w["end"])
            if not math.isfinite(start + end):
                continue
            # Atenção pode atribuir a pausa instrumental à primeira palavra seguinte.
            # Corta a pausa, em vez de espalhar suas sílabas por vários segundos.
            maximum = min(1.6, max(0.24, len(text) * 0.11))
            if end - start > max(2.0, maximum * 2):
                start = end - maximum
            start = max(previous, 0.0, start)
            end = max(start + 0.025, end)
            words.append(
                {
                    "text": text,
                    "start": start,
                    "end": end,
                    "language": language,
                    "line": index,
                    "probability": float(w.get("probability", 0.0)),
                }
            )
            previous = start
    if not words:
        raise ValueError("O alinhador não encontrou palavras no áudio.")
    return words


def align_words(
    audio_path, text, model_size="small", language="pt", progress=None, download_root=None
):
    """Localiza no áudio as palavras da letra fornecida.

    O modelo estima os tempos das palavras; lyrics.py divide cada uma em sílabas.
    O callback progress recebe um número entre 0 e 1."""
    import stable_whisper
    import librosa
    from performance import cpu_threads

    if not text.strip():
        raise ValueError("Informe a letra para alinhamento.")
    if language == "auto":
        raise ValueError("Selecione o idioma para alinhar uma letra existente.")
    model = stable_whisper.load_faster_whisper(
        model_size,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads(),
        num_workers=1,
        **({"download_root": download_root} if download_root else {}),
    )
    y, _ = librosa.load(str(audio_path), sr=16000, mono=True)
    if not len(y):
        raise ValueError("Áudio vazio para alinhamento da letra.")
    callback = (
        (lambda current, total: progress(min(1.0, current / max(total, 0.001))))
        if progress
        else None
    )
    base = dict(
        language=language,
        original_split=True,
        regroup=False,
        fast_mode=True,
        max_word_dur=2.5,
        word_dur_factor=2.5,
        verbose=None,
        progress_callback=callback,
    )
    # Primeiro tenta o modo preciso: ignora trechos longos sem voz (introduções e solos) e
    # ajusta as bordas das palavras ao silêncio real da faixa vocal. Se a combinação não
    # rodar neste ambiente, usa o alinhamento anterior em vez de perder a letra.
    try:
        result = model.align(y, text, nonspeech_skip=NONSPEECH_SKIP_SECONDS, suppress_silence=True, **base)
    except Exception:
        result = model.align(y, text, nonspeech_skip=None, suppress_silence=False, **base)
    return normalize_alignment(result, language)
