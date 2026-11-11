"""Organiza a bateria na grade e mantém o chimbal em uma faixa própria."""

import copy

import numpy as np
import pretty_midi

HIHAT_NOTES = {42, 44, 46}


def quantize_drums(midi, bpm, grid_times, grid=16, hihat_grid_times=None):
    """Alinha ataques da bateria com precisão métrica na grade do BPM da música.

    Garante que bumbo, caixa, chimbal e pratos fiquem perfeitamente no tempo da música,
    eliminando oscilações e atrasos indesejados sem perder viradas rápidas.
    """
    if not grid:
        return midi
    bpm_val = float(bpm) if bpm and float(bpm) > 0 else 120.0
    effective_grid = 32 if (bpm_val < 95.0 and grid == 16) else grid
    step = 60.0 / bpm_val * 4.0 / float(effective_grid)
    # Combine user-provided grid with optional hi‑hat grid for a unified timing reference.
    base_times = np.asarray(grid_times, dtype=float)
    if hihat_grid_times is not None:
        # Merge and deduplicate, keeping sorted order.
        combined = np.unique(np.concatenate([base_times, np.asarray(hihat_grid_times, dtype=float)]))
    else:
        combined = base_times
    times = combined

    # Cria pontos de apoio refinados (subdivisão de fusas / 32avos) para acomodar viradas rápidas
    if len(times) > 1:
        step_half = step / 2.0
        # Interpola meios-passos para viradas rápidas
        times_32 = np.sort(np.unique(np.concatenate([times, times[:-1] + np.diff(times) / 2.0])))
    else:
        times_32 = times

    tom_notes_set = {41, 43, 45, 47, 48, 50}
    tom_times = [n.start for inst in midi.instruments if inst.is_drum for n in inst.notes if n.pitch in tom_notes_set]
    snare_times = [n.start for inst in midi.instruments if inst.is_drum for n in inst.notes if n.pitch in (38, 40)]

    for instrument in midi.instruments:
        if not instrument.is_drum:
            continue
        slots = {}
        for note in instrument.notes:
            # Identifica se a nota faz parte de uma virada real (rufos rápidos de caixa ou múltiplos tons)
            is_tom = note.pitch in tom_notes_set
            is_fill = (
                is_tom and sum(1 for tt in tom_times if abs(note.start - tt) < 0.45) >= 2
            ) or (
                note.pitch in (38, 40) and sum(1 for st in snare_times if abs(note.start - st) < 0.35) >= 3
            )
            is_groove_core = (note.pitch in (35, 36, 38, 40, 42, 44, 46, 49, 51)) and not is_fill

            if is_fill and len(times_32):
                # Viradas de bateria (tons, surdos e caixas rápidas): grade refinada de 32avos
                idx_fine = int(np.searchsorted(times_32, note.start))
                cand_fine = times_32[max(0, idx_fine - 1) : min(len(times_32), idx_fine + 2)]
                start = float(min(cand_fine, key=lambda v: abs(v - note.start)))
            elif len(times):
                idx_main = int(np.searchsorted(times, note.start))
                cand_main = times[max(0, idx_main - 1) : min(len(times), idx_main + 2)]
                start = float(min(cand_main, key=lambda v: abs(v - note.start)))
            else:
                s_step = (step / 4.0) if is_fill else (step / 2.0)
                start = max(0.0, round(note.start / s_step) * s_step)

            key = (note.pitch, round(start, 5))
            previous = slots.get(key)
            if previous is None:
                revised = copy.copy(note)
                revised.start = start
                revised.end = start + max(0.045, note.end - note.start)
                slots[key] = revised
            elif is_fill and abs(note.start - previous.start) >= 0.035:
                # Em viradas rápidas (rufos/rolls), preserva as batidas consecutivas sem descartar
                step_shift = (step / 2.0) if len(times_32) else 0.05
                next_start = start + step_shift
                next_key = (note.pitch, round(next_start, 5))
                if next_key not in slots:
                    revised = copy.copy(note)
                    revised.start = next_start
                    revised.end = next_start + max(0.045, note.end - note.start)
                    slots[next_key] = revised
            elif note.velocity > previous.velocity:
                previous.velocity = note.velocity
                previous.end = max(previous.end, start + max(0.045, note.end - note.start))

        # Sincroniza bumbo, caixa e chimbal que acontecem juntos no mesmo ataque rítmico
        notes = sorted(slots.values(), key=lambda note: (note.start, note.pitch))
        aligned_groove = []
        i = 0
        while i < len(notes):
            group = [notes[i]]
            j = i + 1
            while j < len(notes) and abs(notes[j].start - notes[i].start) < 0.025:
                group.append(notes[j])
                j += 1
            if len(group) > 1:
                # Usa o tempo da peça que caiu mais perfeitamente no tempo (ou bumbo/caixa)
                ref_time = group[0].start
                for g_note in group:
                    if g_note.pitch in (35, 36, 38):
                        ref_time = g_note.start
                        break
                for g_note in group:
                    g_note.start = ref_time
                    g_note.end = max(ref_time + 0.02, g_note.end)
            aligned_groove.extend(group)
            i = j

        previous_by_pitch = {}
        for note in sorted(aligned_groove, key=lambda n: (n.start, n.pitch)):
            previous = previous_by_pitch.get(note.pitch)
            if previous is not None:
                if note.start > previous.start:
                    previous.end = min(previous.end, note.start)
            previous_by_pitch[note.pitch] = note
        instrument.notes = sorted(aligned_groove, key=lambda n: (n.start, n.pitch))
    return midi


def split_hihat(midi):
    """Separa somente as notas de chimbal; ambas as faixas usam canal GM 10.

    Um crash não remove uma nota simultânea de chimbal. Os dois instrumentos
    continuam sendo percussão, sem transformar o chimbal em um timbre melódico.
    """
    hats = []
    instruments = []
    for instrument in midi.instruments:
        if not instrument.is_drum:
            instruments.append(instrument)
            continue
        hats.extend(note for note in instrument.notes if note.pitch in HIHAT_NOTES)
        instrument.notes = [note for note in instrument.notes if note.pitch not in HIHAT_NOTES]
        if instrument.notes:
            instruments.append(instrument)
    if hats:
        hihat = pretty_midi.Instrument(0, is_drum=True, name="Drums - Chimbal")
        hihat.notes = sorted(hats, key=lambda note: (note.start, note.pitch))
        instruments.append(hihat)
    midi.instruments = instruments
    return midi
