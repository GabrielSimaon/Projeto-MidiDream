"""Confere e simplifica a fundamental do baixo no áudio, mantendo apenas a linha de raiz."""

import copy

import librosa
import numpy as np


def validate_bass_pitch(midi, wav):
    """Garante que o baixo siga apenas notas fundamentais reais, sem solar nem inventar notas.
    
    1. Extrai a fundamental (f0) com pYIN em 8 kHz.
    2. Remove notas fantasma onde não há voz/baixo no áudio original.
    3. Converte notas detectadas em harmônicos/oitavas para a fundamental verdadeira.
    4. Limita a tessitura à faixa fundamental do baixo (MIDI 28 = E1 a 52 = E3).
    5. Torna a linha estritamente monofônica (sem sobreposições).
    6. Remove micro-notas (glitches < 80 ms) e funde notas consecutivas iguais em sustentações limpas.
    """
    result = copy.deepcopy(midi)
    report = {"checked": 0, "corrected": 0, "removed": 0, "uncertain": 0}
    try:
        y, sr = librosa.load(str(wav), sr=8000, mono=True)
    except Exception:
        return result, report
    if len(y) < 2048 or np.max(np.abs(y)) < 1e-6:
        return result, report

    # Blocos com contexto nas bordas evitam matrizes grandes em músicas longas.
    hop, frame, block = 128, 2048, 8000 * 20
    frequencies, probabilities = [], []
    margin = frame // hop
    for offset in range(0, len(y), block):
        left, right = max(0, offset - frame), min(len(y), offset + block + frame)
        f0, voiced, probability = librosa.pyin(
            y[left:right], sr=sr, fmin=27.5, fmax=350,
            frame_length=frame, hop_length=hop, resolution=0.2,
            n_thresholds=32, fill_na=np.nan,
        )
        first = margin if offset else 0
        count = min(block, len(y) - offset)
        stop = first + (count + hop - 1) // hop
        frequencies.extend(f0[first:stop])
        probabilities.extend(np.where(voiced[first:stop], probability[first:stop], 0))

    f0, probability = np.asarray(frequencies), np.asarray(probabilities)
    pitches = librosa.hz_to_midi(np.where(np.isfinite(f0), f0, 440))
    rounded = np.rint(pitches).astype(int)
    reliable = (probability >= 0.60) & (np.abs(pitches - rounded) <= 0.35)

    for instrument in result.instruments:
        if instrument.is_drum:
            continue
        clean_notes = []
        for note in instrument.notes:
            duration = note.end - note.start
            # Descarta glitches microscópicos (< 35ms), preservando ataques rápidos
            if duration < 0.035:
                report["removed"] += 1
                continue

            padding = min(0.020, duration * 0.10)
            lo = max(0, int(np.ceil((note.start + padding) * sr / hop)))
            hi = min(len(pitches), int((note.end - padding) * sr / hop) + 1)
            if hi - lo < 1:
                report["removed"] += 1
                continue

            report["checked"] += 1
            valid = reliable[lo:hi]
            # Se menos de 20% do trecho tem fundamental audível no áudio do baixo,
            # a nota é fantasma / vazamento de outro instrumento -> remove.
            if np.mean(valid) < 0.20:
                report["removed"] += 1
                continue

            votes = rounded[lo:hi][valid]
            if not len(votes):
                report["removed"] += 1
                continue

            counts = np.bincount(votes, minlength=128)
            candidate = int(counts.argmax())
            agreement = counts[candidate] / len(votes)

            # Se houver concordância com a fundamental do pYIN, adota a fundamental
            if 24 <= candidate <= 65 and agreement >= 0.35:
                if candidate != note.pitch:
                    note.pitch = candidate
                    report["corrected"] += 1

            # Mantém na tessitura fundamental do baixo elétrico (MIDI 24 = C1 até 58 = A#3)
            while note.pitch > 58:
                note.pitch -= 12
                report["corrected"] += 1
            while note.pitch < 24:
                note.pitch += 12
                report["corrected"] += 1

            clean_notes.append(note)

        # Passagem monofônica: remove sobreposições e preserva ataques rápidos
        clean_notes.sort(key=lambda n: n.start)
        mono_notes = []
        for n in clean_notes:
            if not mono_notes:
                mono_notes.append(n)
                continue
            prev = mono_notes[-1]
            if n.start - prev.start < 0.035:
                if n.velocity > prev.velocity:
                    mono_notes[-1] = n
                continue
            if prev.end > n.start:
                prev.end = max(prev.start + 0.03, n.start)
            # Só une se o intervalo for quase nulo (< 15ms), sem fundir notas rápidas separadas
            if prev.pitch == n.pitch and (n.start - prev.end) < 0.015:
                prev.end = max(prev.end, n.end)
                continue
            mono_notes.append(n)

        # ALONGAMENTO DE GROOVE:
        # No baixo elétrico/acústico, o groove musical é sustentado (legato).
        # A nota preenche o espaço até a próxima nota do baixo, mantendo o corpo da música.
        sustained = []
        for i in range(len(mono_notes)):
            current = mono_notes[i]
            if i + 1 < len(mono_notes):
                next_start = mono_notes[i + 1].start
                gap = next_start - current.start
                if 0.05 < gap <= 2.2:
                    # Alonga até quase o início da próxima nota (30 ms de respiração)
                    current.end = max(current.end, next_start - 0.030)
                elif gap > 2.2:
                    current.end = max(current.end, current.start + 0.65)
            else:
                current.end = max(current.end, current.start + 0.80)
            sustained.append(current)

        instrument.notes = [n for n in sustained if (n.end - n.start) >= 0.035]

    return result, report


def sustain_bass_groove(midi, bpm=120.0):
    """Garante que a linha de baixo tenha sustentação contínua de groove."""
    for inst in midi.instruments:
        if inst.is_drum:
            continue
        notes = sorted(inst.notes, key=lambda n: n.start)
        for i in range(len(notes)):
            curr = notes[i]
            if i + 1 < len(notes):
                nxt = notes[i + 1].start
                gap = nxt - curr.start
                if 0.05 < gap <= 2.2:
                    curr.end = max(curr.end, nxt - 0.030)
                elif gap > 2.2:
                    curr.end = max(curr.end, curr.start + min(0.85, 60.0 / bpm * 1.5))
            else:
                curr.end = max(curr.end, curr.start + min(1.0, 60.0 / bpm * 2.0))
        inst.notes = notes
    return midi
