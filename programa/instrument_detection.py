"""Decisão multilabel: não obriga a escolher um instrumento em cada trecho."""
import math

TARGETS = {
    "accordion": {
        "label": "Sanfona",
        "classes": ("Accordion",),
        "prompt": "The sound of an accordion playing sustained reed notes and bellows chords",
        "program": 21,
        "channel": 5,
        "threshold": 0.20,
    },
    "acoustic_guitar": {
        "label": "Violão",
        "classes": (
            "Acoustic guitar",
            "Guitar",
            "Strum",
            "Plucked string instrument",
            "Steel guitar, slide guitar",
        ),
        "prompt": "The sound of an acoustic guitar playing chords, strumming and plucked strings rhythm accompaniment",
        "program": 25,
        "channel": 6,
        "threshold": 0.15,
    },
    "electric_guitar": {
        "label": "Guitarra elétrica",
        "classes": (
            "Electric guitar",
            "Guitar",
            "Tapping (guitar technique)",
        ),
        "prompt": "The sound of an electric guitar playing amplified strings, chords and riffs",
        "program": 27,
        "channel": 7,
        "threshold": 0.18,
    },
    "piano": {
        "label": "Piano",
        "classes": ("Piano", "Electric piano"),
        "prompt": "The sound of an acoustic piano playing chords and notes",
        "program": 0,
        "channel": 2,
        "threshold": 0.28,
    },
}


def decide_presence(windows, threshold=0.25):
    if not 0 < threshold < 1:
        raise ValueError("Limiar de detecção inválido.")
    report = {}
    for kind, target in TARGETS.items():
        th = target.get("threshold", threshold)
        scores = [max(float(row.get(label, 0)) for label in target["classes"]) for row in windows]
        if any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
            raise ValueError("O detector retornou confiança inválida.")
        hits = sum(score >= th for score in scores)
        peak = max(scores, default=0.0)
        # Detecta o instrumento se houver ao menos 1 janela acima do limiar
        present = (hits >= 1)
        report[kind] = {"label": target["label"], "score": peak, "windows": hits, "present": present}
    return report


def verify_identity(expected, presence):
    """Não troca um timbre por outro: conflito forte devolve áudio ao resíduo."""
    target = presence.get(expected, {})
    score = target.get("score") or 0.0
    rivals = [(kind, row.get("score") or 0.0) for kind, row in presence.items()
              if kind != expected and row.get("present")]
    rival, rival_score = max(rivals, key=lambda pair: pair[1], default=(None, 0.0))
    # Não cria falso conflito entre violão e guitarra elétrica na conferência do timbre
    is_guitar_family = expected in ("acoustic_guitar", "electric_guitar") and rival in ("acoustic_guitar", "electric_guitar")
    if is_guitar_family:
        conflict = False
    else:
        conflict = not target.get("present") and rival_score >= 0.75 and rival_score - score >= 0.35
    return {"accepted": not conflict, "uncertain": not target.get("present"),
            "expected_score": score, "stronger_instrument": rival if conflict else None}


def duplicate_source(audio, accepted, threshold=0.98):
    """Detecta a mesma forma de onda vazando para dois rótulos diferentes."""
    import numpy as np
    for kind, previous in accepted.items():
        size = min(len(audio), len(previous))
        if not size:
            continue
        step = max(1, size // 100000)
        left = np.asarray(audio[:size:step], dtype=np.float64)
        right = np.asarray(previous[:size:step], dtype=np.float64)
        denominator = np.linalg.norm(left) * np.linalg.norm(right)
        if denominator > 1e-10 and abs(float(np.dot(left, right))) / denominator >= threshold:
            return kind
    return None
