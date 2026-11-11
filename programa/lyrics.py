"""Transcrição vocal e criação de eventos de letra para MIDI/KAR.

Os tempos vêm de reconhecimento de fala/voz e são estimativas. Em músicas cantadas,
reverb, backing vocals e sílabas esticadas podem exigir correção manual.
"""

from __future__ import annotations

import copy
import re
import math
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import mido
import pyphen

from midi_timing import TempoMap
from performance import cpu_threads


def transcribe_words(
    audio_path: str | Path,
    model_size: str = "base",
    language: str = "pt",
    fast: bool = False,
    progress=None,
) -> list[dict[str, Any]]:
    """Transcreve a faixa vocal e tenta obter tempos por palavra com faster-whisper."""
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_size, device="cpu", compute_type="int8", cpu_threads=cpu_threads(), num_workers=1
    )
    lang = None if language == "auto" else language
    segments, info = model.transcribe(
        str(audio_path),
        language=lang,
        word_timestamps=True,
        # Em canto, VAD pode classificar trechos musicais como silêncio; por isso fica desligado.
        vad_filter=False,
        condition_on_previous_text=False,
        beam_size=1 if fast else 5,
        best_of=1 if fast else 5,
        temperature=0.0,
    )
    results: list[dict[str, Any]] = []
    for segment in segments:
        if progress:
            progress(min(1.0, float(segment.end) / max(0.001, float(info.duration))))
        # Descarta somente indício combinado de silêncio e transcrição muito incerta.
        if (
            getattr(segment, "no_speech_prob", 0.0) > 0.65
            and getattr(segment, "avg_logprob", 0.0) < -1.0
        ):
            continue
        if getattr(segment, "words", None):
            for word in segment.words:
                text = (word.word or "").strip()
                if text:
                    start = max(0.0, float(word.start or 0.0))
                    end = max(start + 0.035, float(word.end or start + 0.08))
                    results.append(
                        {
                            "text": text,
                            "start": start,
                            "end": end,
                            "language": info.language,
                            "probability": float(getattr(word, "probability", 0.0)),
                        }
                    )
        else:
            text = (segment.text or "").strip()
            duration = max(0.0, float(segment.end) - float(segment.start))
            tokens = _word_tokens(text)
            if tokens:
                step = duration / len(tokens)
                for i, token in enumerate(tokens):
                    results.append(
                        {
                            "text": token,
                            "start": float(segment.start) + i * step,
                            "end": float(segment.start) + (i + 1) * step,
                            "language": info.language,
                        }
                    )
    return results


def _word_tokens(line: str) -> list[str]:
    """Divide o texto em palavras, preservando a pontuação associada."""
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*[^\w\s]*", line, flags=re.UNICODE)


def _norm(text: str) -> str:
    """Remove acentos e diferenças de escrita para comparar palavras."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9']", "", text)


def _interpolate_manual_words(
    manual: list[dict[str, Any]], detected: list[dict[str, Any]], duration: float
) -> list[dict[str, Any]]:
    """Alinha texto digitado aos tempos do Whisper, com interpolação para palavras ausentes."""
    if not manual:
        return []
    if not detected:
        # Sem alinhamento acústico, usa distribuição uniforme como fallback explícito.
        total_chars = sum(max(1, len(_norm(w["text"]))) for w in manual)
        cursor = 0.0
        for word in manual:
            fraction = max(1, len(_norm(word["text"]))) / max(1, total_chars)
            span = max(0.06, duration * fraction)
            word["start"] = min(max(0.0, cursor), max(0.0, duration - 0.04))
            word["end"] = min(duration, max(word["start"] + 0.04, word["start"] + span))
            cursor = word["end"]
        return manual

    a = [_norm(w["text"]) for w in manual]
    b = [_norm(w["text"]) for w in detected]
    matcher = SequenceMatcher(a=a, b=b, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mi, di = block.a + offset, block.b + offset
            manual[mi]["start"] = float(detected[di]["start"])
            manual[mi]["end"] = float(detected[di]["end"])

    known = [i for i, word in enumerate(manual) if "start" in word]
    if not known:
        # Letras com diferenças grandes em relação à transcrição: faz interpolação global.
        return _interpolate_manual_words(manual, [], duration)

    # Preenche trechos sem correspondência entre dois pontos ancorados.
    for left, right in zip(known, known[1:]):
        if right - left <= 1:
            continue
        t0 = float(manual[left]["end"])
        t1 = float(manual[right]["start"])
        if t1 <= t0:
            t0 = float(manual[left]["start"])
            t1 = max(t0 + 0.08 * (right - left), float(manual[right]["start"]))
        count = right - left - 1
        for idx in range(left + 1, right):
            fraction0 = (idx - left - 1) / count
            fraction1 = (idx - left) / count
            manual[idx]["start"] = max(0.0, t0 + (t1 - t0) * fraction0)
            manual[idx]["end"] = max(manual[idx]["start"] + 0.04, t0 + (t1 - t0) * fraction1)

    first = known[0]
    if first > 0:
        total = float(manual[first]["start"])
        step = total / first if first else 0.0
        for idx in range(first - 1, -1, -1):
            manual[idx]["end"] = max(0.04, total - (first - idx - 1) * step)
            manual[idx]["start"] = max(0.0, manual[idx]["end"] - max(0.06, step * 0.8))
    last = known[-1]
    if last < len(manual) - 1:
        tail = max(0.0, duration - float(manual[last]["end"]))
        count = len(manual) - last - 1
        step = tail / count if count else 0.0
        for idx in range(last + 1, len(manual)):
            manual[idx]["start"] = min(
                duration, float(manual[last]["end"]) + (idx - last - 1) * step
            )
            manual[idx]["end"] = min(duration, float(manual[idx]["start"]) + max(0.04, step * 0.8))

    previous = 0.0
    for word in manual:
        start = max(previous, max(0.0, float(word.get("start", 0.0))))
        end = max(start + 0.035, float(word.get("end", start + 0.08)))
        if duration > 0:
            end = min(end, duration)
            start = min(start, max(0.0, duration - 0.035))
            end = max(start + 0.025, end)
        word["start"], word["end"] = start, end
        previous = start
    return manual


def build_lyric_events(
    detected_words: list[dict[str, Any]],
    manual_text: str,
    duration: float,
    language: str = "pt",
) -> tuple[list[tuple[float, str]], str, str]:
    """Retorna eventos (segundos,texto), letra por extenso e método de alinhamento."""
    if language == "auto" and detected_words:
        language = detected_words[0].get("language", "pt")
    lang_for_hyphen = {"pt": "pt_BR", "en": "en_US", "es": "es_ES", "auto": "pt_BR"}.get(
        language, "pt_BR"
    )
    try:
        syllabifier = pyphen.Pyphen(lang=lang_for_hyphen)
    except Exception:
        syllabifier = pyphen.Pyphen(lang="pt_BR")

    words: list[dict[str, Any]] = []
    has_manual = bool(manual_text.strip())
    from music_guide import is_chord_symbol, parse_guide

    if has_manual:
        # Limpa e oculta cifras de manual_text
        clean_manual, _ = parse_guide(manual_text)
        if clean_manual.strip():
            manual_text = clean_manual

        clean_detected = [
            w for w in detected_words
            if not is_chord_symbol(w.get("text", "")) and w.get("text", "").strip() != "[QUEBRA]"
        ]
        for line_idx, line in enumerate(
            manual_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ):
            if is_chord_symbol(line.strip()):
                continue
            for token in _word_tokens(line):
                if not is_chord_symbol(token):
                    words.append({"text": token, "line": line_idx})
        words = _interpolate_manual_words(words, clean_detected, duration)
        method = "letra digitada alinhada por aproximação com os tempos detectados"
    else:
        for i, item in enumerate(detected_words):
            t_text = str(item.get("text", "")).strip()
            if is_chord_symbol(t_text) or t_text == "[QUEBRA]":
                continue
            words.append(
                {
                    "text": item["text"],
                    "start": float(item["start"]),
                    "end": float(item["end"]),
                    "line": 0,
                }
            )
        if not words:
            return [], "", "nenhuma palavra foi reconhecida"
        # Reconstitui linhas por pausas e pontuação, mantendo o texto do modelo.
        line_idx = 0
        prev = None
        for word in words:
            if prev is not None:
                pause = float(word["start"]) - float(prev["end"])
                if pause >= 0.62 or re.search(r"[.!?…]$", prev["text"]):
                    line_idx += 1
            word["line"] = line_idx
            prev = word
        method = "transcrição automática; revisar os tempos e a letra"

    # Linhas curtas para o visor não deixar o refrão inteiro fora da tela.
    display_line = -1
    original_line = None
    width = 0
    for word in words:
        current = word.get("line", 0)
        if current != original_line or width + len(word["text"]) + 1 > 38:
            display_line += 1
            width = 0
        original_line = current
        word["line"] = display_line
        width += len(word["text"]) + 1

    # Gera eventos por sílaba. A divisão de palavras é linguística, não um alinhador
    # fonético; por isso o tempo dentro de cada palavra é interpolado.
    events: list[tuple[float, str, int]] = []
    plain_lines: list[str] = []
    last_line = None
    line_words: dict[int, list[str]] = {}
    for word in words:
        line_words.setdefault(int(word.get("line", 0)), []).append(str(word["text"]))

    sequence = 0
    for i, word in enumerate(words):
        token = str(word["text"])
        start = max(0.0, float(word.get("start", 0.0)))
        end = max(start + 0.04, float(word.get("end", start + 0.12)))
        if duration > 0:
            start = min(start, max(0.0, duration - 0.04))
            end = min(max(start + 0.025, end), max(start + 0.025, duration))
        if last_line is not None and int(word.get("line", 0)) != last_line:
            events.append((start, "\r\n", sequence))
            sequence += 1
        last_line = int(word.get("line", 0))

        # Separa pontuação da parte alfabética para não passá-la ao dicionário de sílabas.
        match = re.match(r"^(.*?)([.,!?;:…]*)$", token)
        core, punctuation = (match.group(1), match.group(2)) if match else (token, "")
        chunks = [part for part in syllabifier.inserted(core).split("-") if part] or [core]
        span = max(0.025, end - start)
        # Sílabas mais longas ocupam mais tempo do que a divisão igual (ex.: "can-ção").
        weights = [max(1, len(re.sub(r"\W", "", part))) for part in chunks]
        total_weight = sum(weights)
        elapsed = 0
        for j, chunk in enumerate(chunks):
            t = start + span * elapsed / total_weight
            elapsed += weights[j]
            lyric = chunk
            if j == len(chunks) - 1:
                lyric += punctuation
                is_last_word = (i == len(words) - 1) or int(words[i + 1].get("line", 0)) != int(
                    word.get("line", 0)
                )
                if not is_last_word:
                    lyric += " "
            events.append((t, lyric, sequence))
            sequence += 1

        if i == len(words) - 1 or int(words[i + 1].get("line", 0)) != int(word.get("line", 0)):
            events.append((end, "\r\n", sequence))
            sequence += 1

    for idx in sorted(line_words):
        plain_lines.append(" ".join(line_words[idx]))
    # Remove quebras duplicadas geradas ao transitar entre linhas com tempos iguais.
    cleaned: list[tuple[float, str, int]] = []
    for event in sorted(events, key=lambda item: (item[0], item[2])):
        if event[1] == "\r\n" and cleaned and cleaned[-1][1] == "\r\n":
            continue
        cleaned.append(event)
    return [(t, text) for t, text, _seq in cleaned], "\r\n".join(plain_lines), method


def add_lyrics_track(
    midi_path: str | Path | mido.MidiFile,
    events: list[tuple[float, str]],
    bpm: float,
    output_path: str | Path,
) -> Path:
    """Escreve FF 05 em Latin-1 respeitando TODOS os eventos de andamento.

    O parâmetro bpm é mantido por compatibilidade; o mapa do MIDI é autoritativo.
    """
    target = Path(output_path)
    if isinstance(midi_path, mido.MidiFile):
        midi = copy.deepcopy(midi_path)
    else:
        source = Path(midi_path)
        midi = mido.MidiFile(source, charset="latin1")
    if midi.type == 2:
        raise ValueError("MIDI tipo 2 não possui uma linha temporal única.")
    midi.type = 1
    tracks = []
    for track in midi.tracks:
        named_lyrics = any(msg.type == "track_name" and msg.name == "Lyrics" for msg in track)
        if named_lyrics and all(msg.is_meta for msg in track) and all(
            msg.type in ("track_name", "instrument_name", "lyrics", "text", "end_of_track")
            for msg in track
        ):
            continue
        cleaned, pending = mido.MidiTrack(), 0
        for msg in track:
            pending += msg.time
            old_text = msg.type == "text" and msg.text.startswith(("/", chr(92)))
            if msg.type == "lyrics" or old_text:
                continue
            cleaned.append(msg.copy(time=pending))
            pending = 0
        if pending:
            cleaned.append(mido.MetaMessage("end_of_track", time=pending))
        tracks.append(cleaned)
    midi.tracks = tracks
    timing = TempoMap(midi)
    lyric_track = mido.MidiTrack()
    lyric_track.append(mido.MetaMessage("track_name", name="Lyrics", time=0))
    lyric_track.append(mido.MetaMessage("instrument_name", name="Synchronized lyrics", time=0))
    timeline: list[tuple[int, int, mido.MetaMessage]] = []
    for sequence, (seconds, text) in enumerate(events):
        if not math.isfinite(float(seconds)) or float(seconds) < 0:
            raise ValueError("Tempo da letra inválido: use segundos não negativos.")
        ticks = timing.seconds_to_ticks(seconds)
        # Meta MIDI é latin-1 para manter acentos comuns do português no VanBasco.
        safe_text = (
            str(text)
            .replace("/", "")
            .replace("\\", "")
            .encode("latin1", errors="replace")
            .decode("latin1")
        )
        timeline.append((ticks, sequence, mido.MetaMessage("lyrics", text=safe_text, time=0)))
    timeline.sort(key=lambda item: (item[0], item[1]))
    last_tick = 0
    for tick, _seq, msg in timeline:
        msg.time = max(0, tick - last_tick)
        lyric_track.append(msg)
        last_tick = tick
    lyric_track.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(lyric_track)
    target.parent.mkdir(parents=True, exist_ok=True)
    midi.save(target)
    return target


def remap_channels(midi_path: str | Path) -> None:
    """Mapeia bateria ao canal MIDI 10 e atribui canais dedicados aos outros stems."""
    path = Path(midi_path)
    midi = mido.MidiFile(path, charset="latin1")
    from instrument_catalog import EXTRA, extra_kind
    for track in midi.tracks:
        track_name = next((m.name for m in track if m.is_meta and m.type == "track_name"), "")
        name = track_name.casefold()
        extra = extra_kind(name)
        if extra:
            channel = EXTRA[extra][2]
        elif any(k in name for k in ("drum", "bateria", "percuss")):
            channel = 9  # Canal 10 na numeração humana (zero-based 9 em MIDI)
        elif "bass" in name or "baixo" in name:
            channel = 0
        elif any(k in name for k in ("vocal", "choir", "voice", "voz")):
            channel = 1
        elif "solo" in name:
            channel = 8
        elif any(k in name for k in ("piano", "keyboard", "teclado")):
            channel = 2
        elif any(k in name for k in ("guitar", "guitarra", "viol")):
            channel = 3
        elif any(k in name for k in ("pad", "synth pad", "cama")):
            channel = 4
        elif name and name not in ("conductor", "lyrics"):
            channel = 7
        else:
            continue
        for i, msg in enumerate(track):
            if not msg.is_meta and hasattr(msg, "channel"):
                track[i] = msg.copy(channel=channel)
    midi.save(path)


def export_karaoke(base_midi, events, output_dir):
    """Reexporta MIDI/KAR a partir do MIDI limpo e dos tempos revisados."""
    output_dir = Path(output_dir)
    from music_guide import is_chord_symbol
    normalized = []
    for event in events:
        seconds = float(event["seconds"])
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Todos os tempos precisam ser finitos e >= 0.")
        if not isinstance(event["text"], str):
            raise ValueError("Cada texto deve ser uma string.")
        text = (
            "\r\n"
            if event["text"] == "[QUEBRA]"
            else event["text"].replace("/", "").replace("\\", "")
        )
        # Oculta qualquer acorde ou cifra para que nunca apareça no karaokê
        clean_tok = text.strip()
        if clean_tok and is_chord_symbol(clean_tok):
            continue
        normalized.append((seconds, text))
    if not normalized:
        raise ValueError("Inclua pelo menos uma sílaba ou quebra de linha.")
    # A ordenação é estável para sílabas e quebras no mesmo instante.
    normalized.sort(key=lambda event: event[0])
    target = output_dir / "karaoke.mid"
    add_lyrics_track(base_midi, normalized, 120, target)
    (output_dir / "karaoke.kar").write_bytes(target.read_bytes())
    return normalized


def read_timed_lyrics(source):
    """Lê FF05 em segundos, respeitando o andamento original do KAR/MIDI."""
    import io

    if isinstance(source, (bytes, bytearray, memoryview)):
        midi = mido.MidiFile(file=io.BytesIO(bytes(source)), charset="latin1")
    else:
        midi = mido.MidiFile(source, charset="latin1")
    if midi.ticks_per_beat <= 0:
        raise ValueError("Karaokê com divisão SMPTE não é suportado.")
    tempo = 500000
    seconds = 0.0
    rows = []
    for msg in mido.merge_tracks(midi.tracks):
        seconds += mido.tick2second(msg.time, midi.ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = msg.tempo
        elif msg.type == "lyrics" and msg.text:
            rows.append(
                {
                    "seconds": float(seconds),
                    "text": "[QUEBRA]" if msg.text in ("\r\n", "\n", "\r") else msg.text,
                }
            )
    if not rows:
        raise ValueError(
            "Esse MIDI/KAR não contém eventos de letra FF05. Importe um karaoke.kar gerado pelo programa ou use o JSON revisado."
        )
    return rows
