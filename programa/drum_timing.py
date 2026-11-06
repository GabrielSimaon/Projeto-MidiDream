"""Organiza a bateria na grade e mantém o chimbal em uma faixa própria."""

import copy

import numpy as np
import pretty_midi

HIHAT_NOTES = {42, 44, 46}


def quantize_drums(midi, bpm, grid_times, grid=16):
    """Alinha ataques da bateria com precisão métrica na grade do BPM da música.

    Garante que bumbo, caixa, chimbal e pratos fiquem perfeitamente no tempo da música,
    eliminando oscilações e atrasos indesejados sem perder viradas rápidas.
    """
    if not grid:
        return midi
    step = 60.0 / float(bpm) * 4.0 / float(grid)
    times = np.asarray(grid_times, dtype=float)

    # Cria pontos de apoio refinados (subdivisão de fusas / 32avos) para acomodar viradas rápidas
    if len(times) > 1:
        step_half = step / 2.0
        # Interpola meios-passos para viradas rápidas
        times_32 = np.sort(np.unique(np.concatenate([times, times[:-1] + np.diff(times) / 2.0])))
    else:
        times_32 = times

    for instrument in midi.instruments:
        if not instrument.is_drum:
            continue
        slots = {}
        for note in instrument.notes:
            is_groove_core = note.pitch in (35, 36, 38, 40, 42, 44, 46, 49, 51)

            if len(times_32):
                # Se for groove principal (bumbo, caixa, chimbal, prato), busca na grade principal de 16avos primeiro
                if is_groove_core and len(times):
                    idx_main = int(np.searchsorted(times, note.start))
                    cand_main = times[max(0, idx_main - 1) : min(len(times), idx_main + 1)]
                    closest_main = float(min(cand_main, key=lambda v: abs(v - note.start)))
                    # Atração firme na grade principal de 16avos (até 48% do passo)
                    if abs(note.start - closest_main) <= step * 0.48:
                        start = closest_main
                    else:
                        # Se estiver mais longe, verifica a subdivisão de 32avos
                        idx_fine = int(np.searchsorted(times_32, note.start))
                        cand_fine = times_32[max(0, idx_fine - 1) : min(len(times_32), idx_fine + 1)]
                        closest_fine = float(min(cand_fine, key=lambda v: abs(v - note.start)))
                        start = closest_fine
                else:
                    # Viradas de tons/surdos: busca na grade refinada de 32avos
                    idx_fine = int(np.searchsorted(times_32, note.start))
                    cand_fine = times_32[max(0, idx_fine - 1) : min(len(times_32), idx_fine + 1)]
                    closest_fine = float(min(cand_fine, key=lambda v: abs(v - note.start)))
                    start = closest_fine
            else:
                start = max(0.0, round(note.start / (step / 2.0)) * (step / 2.0))

            key = (note.pitch, round(start, 5))
            previous = slots.get(key)
            if previous is None or note.velocity > previous.velocity:
                revised = copy.copy(note)
                revised.start = start
                revised.end = start + max(0.02, note.end - note.start)
                slots[key] = revised

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
