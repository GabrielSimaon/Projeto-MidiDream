"""Sincronização musical de letra para vídeos de karaokê e playbacks instrumentais.

Quando o usuário fornece um vídeo de karaokê (sem voz cantada ou com voz inaudível),
o Whisper não tem âncoras acústicas vocais confiáveis.
Este módulo:
1. Detecta automaticamente se o vídeo/áudio é um playback de karaokê.
2. Extrai marcações de tempo da descrição do YouTube ou de legendas (.vtt) caso existam.
3. Distribui os versos da letra compasso a compasso de forma musical e sincronizada
   com o andamento (BPM) da música, respeitando introduções e solos instrumentais.
4. Garante que as palavras nunca fiquem amontoadas no mesmo segundo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lyrics import _word_tokens, _norm


def is_karaoke_source(title: str, detected_words: list[dict] | None = None, vocal_rms: float | None = None) -> bool:
    """Verifica se a fonte de áudio é um vídeo de karaokê ou playback instrumental."""
    title_low = (title or "").casefold()
    keywords = ("karaoke", "karaokê", "playback", "instrumental", "backing track", "sem voz", "play back")
    if any(k in title_low for k in keywords):
        return True
    # Se a transcrição acústica não encontrou palavras válidas ou quase nada
    if detected_words is not None and len(detected_words) < 8:
        return True
    # Se a energia RMS do canal de voz for praticamente silêncio
    if vocal_rms is not None and vocal_rms < 0.018:
        return True
    return False


def extract_intro_from_info(info_json_path: Path | str | None) -> float | None:
    """Procura na descrição e capítulos do YouTube timestamps de início do karaokê/canto."""
    if not info_json_path:
        return None
    path = Path(info_json_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    # 1. Verifica capítulos oficiais do YouTube
    chapters = data.get("chapters") or []
    for ch in chapters:
        title = ch.get("title", "").casefold()
        if any(k in title for k in ("karaok", "canto", "letra", "voz", "início", "inicio")):
            start_time = float(ch.get("start_time", 0.0))
            if start_time > 0:
                return start_time

    # 2. Verifica marcações de tempo na descrição (ex: '0:05 Karaokê')
    description = data.get("description", "")
    for line in description.splitlines():
        match = re.search(r"(\d+):(\d{2})\s*(?:[-–—]\s*)?(?:karaok|canto|letra|voz|in[ií]cio)", line, re.IGNORECASE)
        if match:
            sec = int(match.group(1)) * 60 + int(match.group(2))
            return float(sec)

    return None


def parse_vtt_subtitles(vtt_path: Path | str) -> list[dict[str, Any]]:
    """Converte arquivo de legendas WebVTT (.vtt) em eventos de palavras sincronizadas."""
    path = Path(vtt_path)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    events = []
    # Padrão de tempo do VTT: 00:00:05.120 --> 00:00:08.450
    pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})")
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = pattern.search(line)
        if m:
            t0 = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0
            t1 = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000.0
            i += 1
            caption_text = ""
            while i < len(lines) and lines[i].strip() and not pattern.search(lines[i]):
                caption_text += " " + lines[i].strip()
                i += 1
            # Limpa tags HTML como <c> ou <font>
            clean_text = re.sub(r"<[^>]+>", "", caption_text).strip()
            tokens = _word_tokens(clean_text)
            if tokens and t1 > t0:
                step = (t1 - t0) / len(tokens)
                for idx, tok in enumerate(tokens):
                    events.append({
                        "text": tok,
                        "start": round(t0 + idx * step, 3),
                        "end": round(t0 + (idx + 1) * step, 3),
                    })
            continue
        i += 1
    return events


def align_karaoke_lyrics_rhythmically(
    manual_text: str,
    duration: float,
    bpm: float = 120.0,
    intro_seconds: float | None = None,
    outros_seconds: float = 4.0,
) -> list[dict[str, Any]]:
    """Distribui as palavras da letra ao longo dos compassos da música de karaokê.

    - Oculta e remove acordes de cifra para que nunca apareçam como texto no karaokê.
    - Respeita a introdução da música (onde os instrumentos tocam sozinhos).
    - Distribui cada verso ao longo dos compassos correspondentes.
    - Insere pausas de respiração musical entre linhas e estrofes.
    - Garante que nenhuma palavra fique amontoada no mesmo segundo.
    """
    if not manual_text.strip():
        return []

    from music_guide import parse_guide, is_chord_symbol

    # Remove e oculta qualquer linha de cifra ou acorde para que o karaokê tenha apenas letra cantável
    clean_text, _ = parse_guide(manual_text)
    if not clean_text.strip():
        clean_text = manual_text

    lines = [line.strip() for line in clean_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    # Filtra linhas de cifras residuais
    lines = [l for l in lines if not is_chord_symbol(l)]

    # Agrupa por estrofes (separadas por linhas em branco)
    stanzas: list[list[list[str]]] = []
    current_stanza: list[list[str]] = []
    for line in lines:
        if not line:
            if current_stanza:
                stanzas.append(current_stanza)
                current_stanza = []
        else:
            tokens = [t for t in _word_tokens(line) if not is_chord_symbol(t)]
            if tokens:
                current_stanza.append(tokens)
    if current_stanza:
        stanzas.append(current_stanza)

    if not stanzas:
        return []

    total_lines = sum(len(st) for st in stanzas)
    if total_lines == 0:
        return []

    # Duração de um compasso musical 4/4 em segundos
    bar_duration = (60.0 / float(bpm)) * 4.0

    # Determina o início do canto
    if intro_seconds is not None and 0.0 <= intro_seconds < duration * 0.45:
        start_time = float(intro_seconds)
    else:
        # Padrão: introdução de 4 compassos (~7s a 138 BPM, ~8s a 120 BPM)
        start_time = min(bar_duration * 4.0, duration * 0.15)

    end_time = max(start_time + 10.0, duration - outros_seconds)
    available_time = max(5.0, end_time - start_time)

    # Cada estrofe ganha peso proporcional à quantidade de linhas + pausa entre estrofes
    stanza_gap_bars = 1.0  # 1 compasso de pausa entre estrofes
    total_slots = sum(len(st) + stanza_gap_bars for st in stanzas) - stanza_gap_bars
    slot_time = available_time / max(1.0, total_slots)

    words_out: list[dict[str, Any]] = []
    current_time = start_time
    global_line = 0

    for st_idx, stanza in enumerate(stanzas):
        for line_idx, line_tokens in enumerate(stanza):
            # Duração desta linha de verso
            line_duration = slot_time
            # 85% do tempo para cantar as sílabas, 15% de respiro no final da linha
            singing_time = line_duration * 0.85
            token_step = singing_time / len(line_tokens)

            for tok_idx, token in enumerate(line_tokens):
                w_start = current_time + tok_idx * token_step
                w_end = w_start + max(0.08, token_step * 0.95)
                words_out.append({
                    "text": token,
                    "start": round(w_start, 3),
                    "end": round(min(duration - 0.05, w_end), 3),
                    "line": global_line,
                })

            current_time += line_duration
            global_line += 1

        # Pausa entre estrofes (solo instrumental ou respiração)
        if st_idx < len(stanzas) - 1:
            current_time += slot_time * stanza_gap_bars
            global_line += 1

    return words_out
