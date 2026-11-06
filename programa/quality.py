"""Perfil único de máxima fidelidade: o usuário não escolhe níveis de qualidade.

Tudo que reduzia precisão (modos rápido/express, YIN ultrarrápido, Whisper pequeno,
filtros desativáveis, quantização forçada) foi removido da interface. Estes valores
são usados pelo aplicativo e pela linha de comando.
"""

import os

# Modelo Demucs em 6 faixas: separa bateria, baixo, voz, piano, violão/guitarra e outros.
DEMUCS_MODEL = "htdemucs_6s"
# Maior modelo de reconhecimento/alinhamento que ainda roda em CPU com int8.
WHISPER_MODEL = "medium"
VOCAL_PROGRAM_FLUTE = 73
VOCAL_PROGRAM_CHOIR = 52


def processing_device():
    """Usa a GPU quando existir; o aplicativo de mesa instala só PyTorch para CPU."""
    if os.environ.get("MIDI_STUDIO_DESKTOP") == "1":
        return "cpu"
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def fixed_settings(*, stems_provided=False, vocal_program=VOCAL_PROGRAM_FLUTE, tonality=None):
    """Argumentos de run_pipeline que não dependem de escolha do usuário."""
    return {
        "speed": "quality",
        "preview_seconds": 0,
        "separate": not stems_provided,
        "demucs_model": DEMUCS_MODEL,
        "device": processing_device(),
        # Reconhece o acompanhamento e isola piano e guitarra/violão em faixas separadas.
        "complete_instruments": True,
        "isolate_piano": True,
        "compact_accompaniment": False,
        "accurate_lyrics": True,
        "whisper_model": WHISPER_MODEL,
        "align_lyrics": True,
        "recognize_lyrics": True,
        "note_cleanup": "musical",
        # Alinha as notas dos instrumentos às cifras digitadas pelo usuário e à escala desejada.
        "accompaniment_filter": "chords",
        "harmonic_correction": True,
        "tonality": tonality,
        "rhythm_lock": True,
        "chord_accompaniment": False,
        "quick_pitch": False,
        "vocal_monophonic": True,
        "drum_grid": 16,
        # Grade rítmica de 16 avos (semicolcheias): todos os instrumentos tocam cravados no tempo do BPM
        "grid": 16,
        "ppq": 960,
        "vocal_program": vocal_program,
    }
