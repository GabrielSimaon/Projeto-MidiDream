"""Acrescenta somente letra; notas, canais e mapa de andamento são preservados."""

import math

import mido

from lyrics import add_lyrics_track, build_lyric_events
from music_guide import parse_guide


def lyric_lines(text, start, end):
    plain, _ = parse_guide(text)
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Digite a letra da música.")
    total = sum(len(line) for line in lines)
    rows, cursor = [], float(start)
    for line in lines:
        stop = cursor + (float(end) - float(start)) * len(line) / total
        rows.append({"inicio": cursor, "fim": stop, "texto": line})
        cursor = stop
    return rows


def insert_lyrics(source, destination, rows):
    midi = mido.MidiFile(source, charset="latin1")
    if midi.type == 2:
        raise ValueError("MIDI tipo 2 contém sequências independentes; use um MIDI tipo 0 ou 1.")
    duration, previous, events = midi.length, 0.0, []
    for row in rows:
        start, end = float(row["inicio"]), float(row["fim"])
        text = str(row["texto"]).strip()
        if not text:
            continue
        if not math.isfinite(start + end) or not 0 <= start < end <= duration + 1e-6:
            raise ValueError("Cada linha precisa de início e fim válidos dentro da duração do MIDI.")
        if start < previous - 1e-6:
            raise ValueError("As linhas devem estar em ordem e sem sobreposição.")
        line_events, _, _ = build_lyric_events([], text, end - start)
        if events:
            events.append((start, "\r\n"))
        events.extend((min(end, start + time), word) for time, word in line_events)
        previous = end
    if not events:
        raise ValueError("Digite ao menos uma linha de letra.")
    return add_lyrics_track(source, events, 120, destination)


def align_and_insert_lyrics(source, destination, audio, text, *, language="pt", offset=0.0, progress=None):
    """Localiza o texto no MP3 e adiciona sílabas sem retranscrever os instrumentos."""
    import librosa
    from pipeline import transcribe_words

    plain, _ = parse_guide(text)
    if not plain.strip():
        raise ValueError("Digite a letra para sincronizar com o MP3.")
    if language not in ("pt", "en", "es"):
        raise ValueError("Selecione o idioma da letra.")
    offset = float(offset)
    if not math.isfinite(offset):
        raise ValueError("O ajuste de tempo precisa ser um número válido.")
    midi = mido.MidiFile(source, charset="latin1")
    if midi.type == 2:
        raise ValueError("Use um MIDI tipo 0 ou 1.")
    duration = float(librosa.get_duration(path=str(audio)))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("O MP3 não contém áudio válido.")
    words = transcribe_words(audio, model_size="small", language=language,
                             alignment_text=plain, progress=progress)
    if not words:
        raise ValueError("Não encontrei o canto no áudio. Confira o MP3 e a letra.")
    for word in words:
        start, end = float(word["start"]), float(word["end"])
        if not math.isfinite(start + end) or not 0 <= start < end <= duration + 0.1:
            raise ValueError("O alinhador retornou tempos inválidos. Confira o MP3 e tente novamente.")
    events, _, _ = build_lyric_events(words, plain, duration, language)
    shifted = [(time + offset, lyric) for time, lyric in events]
    if not shifted or any(time < -0.001 or time > midi.length + 0.1 for time, _ in shifted):
        raise ValueError("A letra ficou fora da duração do MIDI. Use o MP3 da mesma versão da música ou ajuste o deslocamento em segundos.")
    shifted = [(min(midi.length, max(0.0, time)), lyric) for time, lyric in shifted]
    add_lyrics_track(source, shifted, 120, destination)
    return {"words": len(words), "syllable_events": sum(text != "\r\n" for _, text in shifted)}
