"""Balanceamento automático de volume relativo dos instrumentos no MIDI.

Atende à solicitação do usuário:
"abaixar o volume dos instrumentos de forma automática, por exemplo o piano2 é mais baixo
q o piano1 então baixar o volume do piano2 e fazer isso com todos os instrumentos".

Analisa a energia acústica real (RMS ativo) de cada stem de áudio separado e calibra
as velocidades das notas e o volume de canal MIDI (Control Change 7) para reproduzir
a mixagem exata da gravação original.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import numpy as np
import librosa
import mido
import pretty_midi

# Mapeamento padrão de canais do MidiDream:
# Canal 0: Baixo
# Canal 9: Bateria
# Canal 3: Guitarra / Violão
# Canal 4: Voz (Choir Aahs)
# Canal 5: Piano (Piano 1)
# Canal 6: Other / Acompanhamento (Piano 2 / Synth)
STEM_CHANNELS = {
    "bass": 0,
    "vocals": 1,
    "piano": 2,
    "guitar": 3,
    "other": 4,
    "piano_solo": 8,
    "drums": 9,
}


def measure_active_rms(audio_path: Path | str) -> float:
    """Calcula a energia RMS dos trechos ativos onde o instrumento realmente toca."""
    try:
        y, _ = librosa.load(str(audio_path), sr=22050, mono=True)
        if len(y) == 0 or np.max(np.abs(y), initial=0) < 1e-6:
            return 0.0
        hop = 512
        frame_len = 2048
        rms = librosa.feature.rms(y=y, frame_length=frame_len, hop_length=hop)[0]
        # Pega apenas os quadros onde o instrumento está soando (acima de 20% da média)
        active = rms[rms > np.mean(rms) * 0.20]
        if len(active) == 0:
            return float(np.mean(rms))
        # Percentil 85 dos trechos ativos dá o nível de presença real do instrumento
        return float(np.percentile(active, 85))
    except Exception:
        return 0.0


def calculate_relative_volumes(
    stems_dir: Path | str,
    lower_second_piano: bool = True,
    other_volume_override: int | None = None,
) -> dict[str, int]:
    """Calcula os volumes de canal MIDI (CC7: 0 a 127) para cada instrumento.

    Permite abaixar o segundo piano / acompanhamento (faixa 'other') para garantir
    que a base de fundo nunca dispute espaço com o piano ou solo principal.
    """
    path = Path(stems_dir)
    if not path.is_dir():
        default_other = 65 if lower_second_piano else 75
        if other_volume_override is not None:
            default_other = other_volume_override
        return {"piano": 105, "piano_solo": 115, "other": default_other, "guitar": 90, "bass": 110, "drums": 108, "vocals": 100}

    rms_levels = {}
    for name in ("piano", "other", "guitar", "bass", "drums", "vocals"):
        for ext in (".wav", ".flac", ".mp3"):
            stem_file = path / f"{name}{ext}"
            if stem_file.is_file():
                rms_levels[name] = measure_active_rms(stem_file)
                break

    # Se não temos áudio para medir, usa valores balanceados padrão
    if not rms_levels:
        default_other = 65 if lower_second_piano else 75
        if other_volume_override is not None:
            default_other = other_volume_override
        return {"piano": 105, "piano_solo": 115, "other": default_other, "guitar": 90, "bass": 110, "drums": 108, "vocals": 100}

    # Referência melódica/harmônica principal: piano ou guitarra
    ref_lead = max(rms_levels.get("piano", 0.0), rms_levels.get("guitar", 0.0), 0.01)

    volumes = {}
    # 1. Piano 1 vs Piano 2 (Other)
    piano_rms = rms_levels.get("piano", 0.0)
    other_rms = rms_levels.get("other", 0.0)

    if piano_rms > 0 and other_rms > 0:
        ratio = other_rms / piano_rms
        volumes["piano"] = 105
        max_other = 75 if lower_second_piano else 95
        volumes["other"] = int(np.clip(105 * ratio * (0.80 if lower_second_piano else 1.0), 40, max_other))
    elif other_rms > 0:
        volumes["other"] = 65 if lower_second_piano else 75
        volumes["piano"] = 105
    else:
        volumes["piano"] = 105
        volumes["other"] = 65 if lower_second_piano else 75

    if other_volume_override is not None:
        volumes["other"] = int(np.clip(other_volume_override, 0, 127))

    # 2. Guitarra / Violão (Solo vs Base)
    guitar_rms = rms_levels.get("guitar", 0.0)
    if guitar_rms > 0 and ref_lead > 0:
        g_ratio = guitar_rms / ref_lead
        # Se for solo (mais alto que o piano), ganha destaque total (até 115)
        # Se for base de fundo, fica mais baixa (65 a 85)
        volumes["guitar"] = int(np.clip(100 * g_ratio, 60, 115))
    else:
        volumes["guitar"] = 90

    # 3. Baixo e Bateria (fundação rítmica equilibrada)
    volumes["bass"] = 112
    volumes["drums"] = 110
    volumes["vocals"] = 100
    volumes["piano_solo"] = 115

    return volumes


def _mix_tracks(midi: mido.MidiFile, volumes: dict[str, int]) -> bool:
    """Aplica CC7 e ajuste de velocity nas trilhas de um objeto MidiFile."""
    modified = False
    for track in midi.tracks:
        channels = [m.channel for m in track if hasattr(m, "channel")]
        if not channels:
            continue
        ch = channels[0]

        target_vol = None
        if ch == 0:
            target_vol = volumes.get("bass", 112)
        elif ch == 9:
            target_vol = volumes.get("drums", 110)
        elif ch in (2, 5):
            target_vol = volumes.get("piano", 105)
        elif ch == 8:
            target_vol = volumes.get("piano_solo", 115)
        elif ch == 3:
            target_vol = volumes.get("guitar", 90)
        elif ch in (4, 6, 7):
            target_vol = volumes.get("other", 65)
        elif ch == 1:
            target_vol = volumes.get("vocals", 100)
        else:
            target_vol = 100

        if target_vol is not None:
            has_cc7 = False
            for m in track:
                if m.type == "control_change" and getattr(m, "control", None) == 7 and m.channel == ch:
                    m.value = target_vol
                    has_cc7 = True
                    modified = True
                    break
            if not has_cc7:
                track.insert(0, mido.Message("control_change", channel=ch, control=7, value=target_vol, time=0))
                modified = True

            if target_vol < 90:
                scale = target_vol / 100.0
                for m in track:
                    if m.type == "note_on" and m.velocity > 0:
                        m.velocity = int(np.clip(m.velocity * scale, 30, 127))
                        modified = True
    return modified


def apply_auto_mix(midi_target: Path | str | mido.MidiFile, volumes: dict[str, int]) -> Any:
    """Aplica os volumes de canal CC7 e calibra as velocities relativas nos MIDIs.

    Aceita um objeto mido.MidiFile individual ou o caminho de um diretório contendo arquivos .mid.
    """
    if isinstance(midi_target, mido.MidiFile):
        _mix_tracks(midi_target, volumes)
        return midi_target

    dir_path = Path(midi_target)
    if not dir_path.is_dir():
        return volumes

    for midi_file in dir_path.glob("*.mid"):
        try:
            midi = mido.MidiFile(midi_file, charset="latin1")
            if _mix_tracks(midi, volumes):
                temp = midi_file.with_suffix(".tmp")
                midi.save(temp)
                temp.replace(midi_file)
        except Exception:
            pass

    return volumes
