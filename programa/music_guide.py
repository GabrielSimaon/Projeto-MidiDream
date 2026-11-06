"""Leitura de cifras comuns, com ou sem colchetes, e acompanhamento opcional."""

import html
import math
import re
import unicodedata

import mido

from midi_timing import TempoMap
from lyrics import _word_tokens, _interpolate_manual_words

ROOTS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ROOT_PATTERN = re.compile(r"([A-G])([#b]?)(.*)")
MODIFIER = re.compile(
    r"maj7|M7|7M|maj|sus2|sus4|dim|aug|add(?:13|11|9|6|4|2)|"
    r"[#b](?:13|11|9|5)|13|11|9|7|6|5|4|2|m|M"
)
DEGREES = {2: 2, 4: 5, 5: 7, 6: 9, 7: 10, 9: 14, 11: 17, 13: 21}
# Dentro de uma frase, estas palavras também podem ser texto normal em português.
AMBIGUOUS_WORDS = {"A", "E", "Em"}
SEPARATORS = {"|", "||", ":|", "|:", "-", "–", "—"}


def chord_notes(symbol):
    """Converte cifras como G4, G7M, F#m7(b5) e C/G em notas MIDI.

    4 significa sus4; 7M, M7 e maj7 significam sétima maior.
    Extensões 9/11/13 incluem a sétima; add9 adiciona só a nona à tríade.
    """
    original = symbol
    symbol = re.sub(r"\s+", "", symbol).replace("♯", "#").replace("♭", "b")
    symbol = symbol.replace("°", "dim").replace("º", "dim").replace("Δ", "maj7")
    symbol = symbol.replace("7+", "7M").replace("+", "aug")
    bass = None
    inversion = re.search(r"/([A-G])([#b]?)$", symbol)
    if inversion:
        bass = (inversion.group(1), inversion.group(2))
        symbol = symbol[: inversion.start()]
    symbol = symbol.replace("6/9", "6add9")
    match = ROOT_PATTERN.fullmatch(symbol)
    if not match:
        raise ValueError("Cifra não reconhecida: " + original)
    root, accidental, body = match.groups()
    if body.count("(") != body.count(")"):
        raise ValueError("Confira os parênteses da cifra: " + original)
    body = body.replace("(", "").replace(")", "").replace(",", "")
    modifiers = []
    cursor = 0
    for token in MODIFIER.finditer(body):
        if token.start() != cursor:
            raise ValueError("Cifra não reconhecida: " + original)
        modifiers.append(token.group())
        cursor = token.end()
    if cursor != len(body):
        raise ValueError("Cifra não reconhecida: " + original)
    quality = {kind for kind in ("m", "dim", "aug") if kind in modifiers}
    if len(quality) > 1:
        raise ValueError("Qualidade de acorde conflitante: " + original)
    third = 3 if "m" in quality or "dim" in quality else 4
    fifth = 6 if "dim" in quality else 8 if "aug" in quality else 7
    intervals = {0, third, fifth}
    if "5" in modifiers:
        intervals.discard(third)
    for token, interval in (("2", 2), ("sus2", 2), ("4", 5), ("sus4", 5)):
        if token in modifiers:
            intervals.discard(third)
            intervals.add(interval)
    major_seventh = any(token in modifiers for token in ("7M", "M7", "maj7")) or (
        any(token in modifiers for token in ("maj", "M"))
        and any(token in modifiers for token in ("9", "11", "13"))
    )
    if major_seventh:
        intervals.add(11)
    elif any(token in modifiers for token in ("7", "9", "11", "13")):
        intervals.add(9 if "dim" in quality else 10)
    if "6" in modifiers:
        intervals.add(9)
    for extension in (9, 11, 13):
        if str(extension) in modifiers:
            intervals.update(DEGREES[degree] for degree in (9, 11, 13) if degree <= extension)
    for token in modifiers:
        if token.startswith("add"):
            intervals.add(DEGREES[int(token[3:])])
        elif token[0] in "#b":
            degree = int(token[1:])
            normal = DEGREES[degree]
            if degree == 5:
                intervals.discard(fifth)
            else:
                intervals.discard(normal)
            intervals.add(normal + (1 if token[0] == "#" else -1))
    base = 48 + ROOTS[root] + {"#": 1, "b": -1, "": 0}[accidental]
    notes = {base + interval for interval in intervals}
    if bass:
        notes.add(36 + ROOTS[bass[0]] + {"#": 1, "b": -1, "": 0}[bass[1]])
    return sorted(notes)


def chord_root_and_bass(symbol):
    """Retorna (tônica_pc, baixo_pc) para a cifra (0 = C, 1 = C#, ..., 11 = B)."""
    symbol = re.sub(r"\s+", "", symbol).replace("♯", "#").replace("♭", "b")
    bass = None
    inversion = re.search(r"/([A-G])([#b]?)$", symbol)
    if inversion:
        bass = (inversion.group(1), inversion.group(2))
        symbol = symbol[: inversion.start()]
    match = ROOT_PATTERN.match(symbol)
    if not match:
        return 0, 0
    root, accidental, _ = match.groups()
    root_val = (ROOTS[root] + {"#": 1, "b": -1, "": 0}[accidental]) % 12
    if bass:
        bass_val = (ROOTS[bass[0]] + {"#": 1, "b": -1, "": 0}[bass[1]]) % 12
    else:
        bass_val = root_val
    return root_val, bass_val


def chord_triad_pcs(symbol: str) -> tuple[int, set[int]]:
    """Retorna a fundamental e o conjunto das 3 notas da tríade fundamental do acorde.
    
    Mesmo para acordes estendidos como F9, Am7, Em7, C9 ou F7M, a harmonia base
    necessita obrigatoriamente da tríade fundamental completa {Fundamental, Terça, Quinta}
    para que detectores e players (como Acordes.exe/vanBasco) não confundam o acorde
    com suspensões acidentais (Csus2, Csus4, etc.).
    """
    sym = re.sub(r"\s+", "", symbol).replace("♯", "#").replace("♭", "b")
    inversion = re.search(r"/([A-G])([#b]?)$", sym)
    if inversion:
        sym = sym[: inversion.start()]
    match = ROOT_PATTERN.match(sym)
    if not match:
        return 0, {0, 4, 7}
    r_str, acc, body = match.groups()
    root = (ROOTS[r_str] + {"#": 1, "b": -1, "": 0}[acc]) % 12

    body_lower = body.lower()
    is_sus4 = "sus4" in body_lower or (body.startswith("4") and not body.startswith("4#"))
    is_sus2 = "sus2" in body_lower
    is_dim = "dim" in body_lower or "°" in body or "º" in body
    is_aug = "aug" in body_lower or "+" in body

    is_minor = False
    if not is_sus4 and not is_sus2:
        if is_dim:
            is_minor = True
        elif "m" in body:
            clean = body.replace("maj", "").replace("Maj", "").replace("7M", "").replace("M7", "").replace("dim", "")
            if "m" in clean.lower():
                is_minor = True

    if is_sus4:
        third = 5
    elif is_sus2:
        third = 2
    elif is_minor:
        third = 3
    else:
        third = 4

    if is_dim:
        fifth = 6
    elif is_aug:
        fifth = 8
    else:
        fifth = 7

    triad = {(root + 0) % 12, (root + third) % 12, (root + fifth) % 12}
    return root, triad


STANDARD_KEYS = (
    # Tonalidades Maiores
    ("C maior", 0, (0, 2, 4, 5, 7, 9, 11)),
    ("G maior", 7, (0, 2, 4, 5, 7, 9, 11)),
    ("D maior", 2, (0, 2, 4, 5, 7, 9, 11)),
    ("A maior", 9, (0, 2, 4, 5, 7, 9, 11)),
    ("E maior", 4, (0, 2, 4, 5, 7, 9, 11)),
    ("B maior", 11, (0, 2, 4, 5, 7, 9, 11)),
    ("F# maior", 6, (0, 2, 4, 5, 7, 9, 11)),
    ("Db maior", 1, (0, 2, 4, 5, 7, 9, 11)),
    ("Ab maior", 8, (0, 2, 4, 5, 7, 9, 11)),
    ("Eb maior", 3, (0, 2, 4, 5, 7, 9, 11)),
    ("Bb maior", 10, (0, 2, 4, 5, 7, 9, 11)),
    ("F maior", 5, (0, 2, 4, 5, 7, 9, 11)),
    # Tonalidades Menores
    ("A menor", 9, (0, 2, 3, 5, 7, 8, 10)),
    ("E menor", 4, (0, 2, 3, 5, 7, 8, 10)),
    ("B menor", 11, (0, 2, 3, 5, 7, 8, 10)),
    ("F# menor", 6, (0, 2, 3, 5, 7, 8, 10)),
    ("C# menor", 1, (0, 2, 3, 5, 7, 8, 10)),
    ("G# menor", 8, (0, 2, 3, 5, 7, 8, 10)),
    ("D# menor", 3, (0, 2, 3, 5, 7, 8, 10)),
    ("Bb menor", 10, (0, 2, 3, 5, 7, 8, 10)),
    ("F menor", 5, (0, 2, 3, 5, 7, 8, 10)),
    ("C menor", 0, (0, 2, 3, 5, 7, 8, 10)),
    ("G menor", 7, (0, 2, 3, 5, 7, 8, 10)),
    ("D menor", 2, (0, 2, 3, 5, 7, 8, 10)),
)


def infer_tonality_from_chords(marks):
    """Infere a tonalidade principal (ex: 'D maior', 'B menor') com análise diatônica rigorosa."""
    if not marks:
        return None

    parsed_chords = []
    for mark in marks:
        sym = mark.get("symbol", "")
        try:
            notes = {n % 12 for n in chord_notes(sym)}
            root, _ = chord_root_and_bass(sym)
            parsed_chords.append((sym, root, notes))
        except Exception:
            continue
    if not parsed_chords:
        return None

    all_pitch_counts = [0] * 12
    root_counts = [0] * 12
    for _, root, notes in parsed_chords:
        root_counts[root] += 1
        for n in notes:
            all_pitch_counts[n] += 1

    first_root = parsed_chords[0][1]
    last_root = parsed_chords[-1][1]

    best_key = None
    best_score = -999999

    for key_name, root_pc, formula in STANDARD_KEYS:
        scale = {(root_pc + degree) % 12 for degree in formula}
        in_scale_notes = sum(all_pitch_counts[p] for p in scale)
        out_scale_notes = sum(all_pitch_counts[p] for p in range(12) if p not in scale)

        fully_diatonic_chords = sum(1 for _, _, notes in parsed_chords if notes.issubset(scale))
        conflicting_chords = len(parsed_chords) - fully_diatonic_chords

        # Penalização severa para acordes com notas fora da escala
        score = (in_scale_notes * 3) - (out_scale_notes * 10)
        score += (fully_diatonic_chords * 12) - (conflicting_chords * 30)

        # Bônus para tônica presente nas raízes e nos extremos da música
        score += root_counts[root_pc] * 4
        if root_pc == first_root:
            score += 3
        if root_pc == last_root:
            score += 3

        if score > best_score:
            best_score = score
            best_key = key_name

    return best_key


def _is_heading(text):
    """Reconhece rótulos de partes; eles não devem virar acordes ou letra cantada."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = " ".join(text.replace("-", " ").split())
    return bool(
        re.fullmatch(
            r"(?:intro|introducao|refrao|pre refrao|ponte|solo|final|instrumental|"
            r"interludio|estrofe|verso|riff|dedilhado|"
            r"(?:primeira|segunda|terceira|quarta|quinta|\d+[aª]?) parte)(?: \d+)?|\d+x",
            text,
        )
    )


def is_chord_symbol(token: str) -> bool:
    """Verifica se um token isolado representa estritamente um acorde de cifra musical."""
    if not token or not isinstance(token, str):
        return False
    clean = token.strip().strip(".,;:!?()[]{}|/ -")
    if not clean:
        return False
    if clean.startswith("[") and clean.endswith("]"):
        clean = clean[1:-1].strip().split()[-1]
    try:
        chord_notes(clean)
        return True
    except Exception:
        return False


def _chord_tag(token):
    """Retorna uma cifra validada no formato interno, ou None para texto normal."""
    token = token.strip()
    if token.startswith("[") and token.endswith("]"):
        content = token[1:-1].strip()
        pieces = content.split()
        if not pieces:
            return None
        symbol = pieces[-1]
        chord_notes(symbol)
        return "[" + content + "]"
    try:
        chord_notes(token)
    except ValueError:
        if re.fullmatch(
            r"[A-G][#b]?(?:m|maj|M|sus|dim|aug|add)?[0-9#b()+/,]+[A-Za-z0-9#b()+/,]*",
            token,
        ):
            raise ValueError("Cifra não reconhecida: " + token) from None
        return None
    return "[" + token + "]"


def normalize_guide(text):
    """Aceita cifras acima da letra e cifras não ambíguas no meio de uma frase.

    A coluna do acorde é ligada à palavra mais próxima na linha seguinte.
    Isso preserva a associação com a letra e OCULTA completamente os acordes da letra cantada.
    """
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    # Rótulos conhecidos também podem aparecer antes dos acordes na mesma linha.
    text = re.sub(r"\[([^]\n]+)\]", lambda match: "" if _is_heading(match[1]) else match[0], text)
    lines = [line.expandtabs(4).rstrip("\\") for line in text.split("\n")]
    chord_lines = {}
    headings = set()
    for index, line in enumerate(lines):
        heading = re.fullmatch(r"\s*\[([^]\n]+)\]\s*", line)
        if heading and (_is_heading(heading[1]) or heading[1] == "QUEBRA"):
            headings.add(index)
            continue
        raw_tokens = list(re.finditer(r"\[[^]\n]+\]|[^\s]+", line))
        # Remove separadores musicais e marcações de repetição como (2x), (4x)
        tokens = [
            t for t in raw_tokens
            if t.group() not in SEPARATORS
            and not re.fullmatch(r"\(?\d+x\)?", t.group(), re.IGNORECASE)
        ]
        chords = []
        valid = bool(tokens)
        for token in tokens:
            tag = _chord_tag(token.group())
            if tag is None:
                # Rótulos de introdução/solo na linha de acordes não invalidam os acordes
                if _is_heading(token.group().strip("[]:")):
                    continue
                valid = False
                break
            chords.append((token.start(), tag))
        if valid and chords:
            chord_lines[index] = chords

    insertions = {}
    replaced = set()
    for index, chords in chord_lines.items():
        # Linha de cifra é sempre ocultada do texto da letra
        replaced.add(index)
        target = next(
            (
                j
                for j in range(index + 1, len(lines))
                if j not in chord_lines and j not in headings and lines[j].strip()
            ),
            None,
        )
        if target is None:
            # Se não houver linha de letra à frente, associa à última linha de letra anterior
            prev_target = next(
                (
                    j
                    for j in range(index - 1, -1, -1)
                    if j not in chord_lines and j not in headings and lines[j].strip()
                ),
                None,
            )
            if prev_target is not None:
                for column, tag in chords:
                    insertions.setdefault(prev_target, []).append((len(lines[prev_target]), tag))
            continue
        words = list(re.finditer(r"\S+", lines[target]))
        for column, tag in chords:
            position = (
                0
                if "[" in lines[target] or not words
                else min(words, key=lambda word: abs(word.start() - column)).start()
            )
            insertions.setdefault(target, []).append((position, tag))

    for index, marks in insertions.items():
        parts = []
        cursor = 0
        for position, tag in sorted(marks, key=lambda item: item[0]):
            parts.extend((lines[index][cursor:position], tag))
            cursor = position
        parts.append(lines[index][cursor:])
        lines[index] = "".join(parts)

    result = []
    for index, line in enumerate(lines):
        if index in headings or index in replaced:
            continue
        # Colchetes explícitos continuam funcionando. A/E/Em em frases são palavras.
        parts = []
        cursor = 0
        for token in re.finditer(r"\S+", line):
            value = token.group()
            if "[" in value or value.strip(".,!?;:") in AMBIGUOUS_WORDS:
                continue
            tag = _chord_tag(value)
            if tag:
                parts.extend((line[cursor : token.start()], tag))
                cursor = token.end()
        parts.append(line[cursor:])
        result.append("".join(parts))
    return "\n".join(result)


def parse_guide(text):
    """Extrai a letra limpa e marcas de acorde, aceitando os dois formatos de cifra."""
    text = normalize_guide(text)
    parts = []
    marks = []
    last = 0
    for match in re.finditer(r"\[([^]\n]+)\]", text):
        token = match[1].strip()
        pieces = token.split()
        symbol = pieces[-1]
        chord_notes(symbol)
        stamp = None
        if len(pieces) == 2:
            time_match = re.fullmatch(r"(\d+):(\d+(?:\.\d+)?)", pieces[0])
            if not time_match:
                raise ValueError("Use um tempo como 00:12.5 antes da cifra.")
            stamp = int(time_match[1]) * 60 + float(time_match[2])
            if not math.isfinite(stamp):
                raise ValueError("Tempo de cifra inválido.")
        elif len(pieces) != 1:
            raise ValueError("Use uma cifra como G7M ou [00:12.5 Am].")
        parts.append(text[last : match.start()])
        marks.append({"symbol": symbol, "offset": len("".join(parts)), "seconds": stamp})
        last = match.end()
    parts.append(text[last:])
    raw_plain = "".join(parts)

    # Oculta qualquer linha remanescente que contenha apenas acordes/cifras isoladas
    clean_lines = []
    for line in raw_plain.split("\n"):
        stripped = line.strip()
        if is_chord_symbol(stripped):
            continue
        clean_lines.append(line)
    return "\n".join(clean_lines), marks


def add_chord_track(path, text, marks, detected, duration):
    """Adiciona uma faixa opcional de acordes nos tempos da letra ou das marcações."""
    words = [{"text": w} for w in _word_tokens(text)]
    aligned = _interpolate_manual_words(words, detected, duration)
    events = []
    for mark in marks:
        index = len(_word_tokens(text[: mark["offset"]]))
        time = mark["seconds"]
        if time is None:
            time = aligned[min(index, len(aligned) - 1)]["start"] if aligned else 0
        if time < duration:
            events.append((max(0, time), mark["symbol"]))
    events.sort()
    midi = mido.MidiFile(path, charset="latin1")
    clock = TempoMap(midi)
    track = mido.MidiTrack(
        [
            mido.MetaMessage("track_name", name="Cifras - guia"),
            mido.Message("program_change", channel=5, program=0),
        ]
    )
    messages = []
    for i, (start, symbol) in enumerate(events):
        end = events[i + 1][0] if i + 1 < len(events) else duration
        if end <= start:
            continue
        messages.append((clock.seconds_to_ticks(start), 1, mido.MetaMessage("marker", text=symbol)))
        for note in chord_notes(symbol):
            messages.extend(
                [
                    (
                        clock.seconds_to_ticks(start),
                        2,
                        mido.Message("note_on", channel=5, note=note, velocity=65),
                    ),
                    (
                        clock.seconds_to_ticks(end),
                        0,
                        mido.Message("note_off", channel=5, note=note, velocity=0),
                    ),
                ]
            )
    cursor = 0
    for tick, _, msg in sorted(messages, key=lambda e: (e[0], e[1])):
        track.append(msg.copy(time=tick - cursor))
        cursor = tick
    midi.tracks.append(track)
    midi.save(path)


def validate_required_inputs(guide, bpm=None):
    """Valida entradas da interface antes de criar pasta ou iniciar conversão."""
    value = None
    if bpm is not None and str(bpm).strip():
        try:
            value = float(bpm)
        except (TypeError, ValueError):
            raise ValueError("Informe um BPM válido entre 30 e 300 (ou deixe em branco para detecção automática).") from None
        if not math.isfinite(value) or not 30 <= value <= 300:
            raise ValueError(
                "Informe um BPM válido entre 30 e 300 (ou deixe em branco para detecção automática)."
            )
    if not isinstance(guide, str) or not guide.strip():
        raise ValueError("Cole ou importe a letra com as cifras na linha de cima, como G e D.")
    text, marks = parse_guide(guide)
    if "[" in text or "]" in text:
        raise ValueError("Confira os colchetes das cifras. Use [C], [Am] ou [00:12.5 G].")
    if not marks:
        raise ValueError("Inclua cifras como G, Am ou G7 acima da letra. Colchetes são opcionais.")
    if not any(char.isalpha() for char in text):
        raise ValueError("Inclua a letra da música junto das cifras; somente acordes não bastam.")
    if any(mark["seconds"] is not None and not math.isfinite(mark["seconds"]) for mark in marks):
        raise ValueError("Os tempos das cifras precisam ser números finitos.")
    return value
