"""Sincroniza bumbo ao baixo, preserva o baixo contínuo e arranja acordes na grade."""

import copy
import numpy as np


def lock_rhythm(instruments, bpm, grid_times=None):
    """Sincroniza o bumbo ao baixo e alinha o ritmo na métrica do BPM, preservando âncora de fase."""
    bpm_val = float(bpm) if bpm and float(bpm) > 0 else 120.0
    # Se o BPM informado ou detectado for em half-time (< 95), ajusta a divisão para manter a resolução real da música
    div = 4.0 if bpm_val < 95.0 else 2.0
    step = 60.0 / bpm_val / div

    times_arr = None
    anchor = 0.0
    if grid_times is not None and len(grid_times) > 1:
        times_arr = np.asarray(grid_times, dtype=float)
        anchor = float(times_arr[0])

    def snap(t):
        if times_arr is not None and len(times_arr):
            idx = int(np.searchsorted(times_arr, t))
            cands = times_arr[max(0, idx - 1) : min(len(times_arr), idx + 2)]
            if len(cands) > 0:
                closest = float(min(cands, key=lambda v: abs(v - t)))
                # Apenas atrai se a nota estiver dentro da janela métrica razoável (evita deformar síncopes)
                if abs(closest - t) <= step * 0.70:
                    return closest
        return max(0.0, anchor + round((t - anchor) / step) * step)

    drum_inst = next((i for i in instruments if i.is_drum), None)
    kicks = sorted(
        {
            round(n.start, 6)
            for i in instruments
            if i.is_drum
            for n in i.notes
            if n.pitch in (35, 36)
        }
    )

    report = {}
    final_bass_starts = []

    for inst in instruments:
        name = inst.name.casefold()
        bass = "bass" in name or "baixo" in name
        guitar = any(x in name for x in ("guitar", "viol", "steel"))
        if inst.is_drum or not (bass or guitar):
            continue
        notes = sorted(inst.notes, key=lambda n: (n.start, n.pitch))
        out = []
        if bass:
            # Preserva os ataques detectados do baixo e alinha com os ataques do bumbo
            # Ignora ataques fantasmas na introdução antes do início real da bateria/baixo
            first_kick = kicks[0] if kicks else 0.0
            attacks = sorted(set(kicks) | {snap(n.start) for n in notes})
            previous = None
            for t in attacks:
                options = [
                    n
                    for n in notes
                    if n.start <= t + (0.12 if kicks else step / 2 + 0.001) and n.end >= t + 0.035
                ]
                if not options:
                    continue
                n = max(
                    options,
                    key=lambda n: (
                        n.velocity
                        / (1 + abs(n.start - t) / step)
                        * (1.15 if previous is not None and abs(n.pitch - previous) <= 7 else 1)
                        * (0.65 if n.pitch > 60 else 1)
                    ),
                )
                new = copy.copy(n)
                new.start = t
                new.end = max(t + 0.06, n.end)
                if out:
                    out[-1].end = min(out[-1].end, t)
                out.append(new)
                previous = n.pitch
            if out:
                # Sustentação contínua de groove do baixo (elimina paradas repentinas)
                step_beat = 60.0 / float(bpm)
                for i in range(len(out)):
                    curr = out[i]
                    if i + 1 < len(out):
                        nxt = out[i + 1].start
                        gap = nxt - curr.start
                        if 0.05 < gap <= 2.2:
                            curr.end = max(curr.end, nxt - 0.030)
                        elif gap > 2.2:
                            curr.end = max(curr.end, curr.start + min(0.85, step_beat * 1.5))
                    else:
                        curr.end = max(curr.end, curr.start + min(1.0, step_beat * 2.0))
                final_bass_starts = [n.start for n in out]
        else:
            # Agrupa os ataques de guitarra na grade métrica respeitando a âncora
            buckets = {}
            for n in notes:
                t = snap(n.start)
                buckets.setdefault(t, []).append(n)
            active = {}
            bucket_times = sorted(buckets.keys())
            for idx_t, t in enumerate(bucket_times):
                nxt_t = bucket_times[idx_t + 1] if idx_t + 1 < len(bucket_times) else t + step * 4
                by_pitch = {}
                for n in buckets[t]:
                    if n.pitch not in by_pitch or n.velocity > by_pitch[n.pitch].velocity:
                        by_pitch[n.pitch] = n
                # Quatro alturas simultâneas; não repete a nota enquanto ela soa.
                active = {p: n for p, n in active.items() if n.end > t}
                ranked = sorted(
                    by_pitch.values(),
                    key=lambda n: n.velocity * np.sqrt(min(0.4, n.end - n.start)),
                    reverse=True,
                )
                for n in ranked:
                    raw_dur = max(step, n.end - n.start)
                    dur = max(step, min(nxt_t - t, max(step * 2.0, snap(raw_dur))))
                    end = max(t + step, t + snap(dur))
                    previous = active.get(n.pitch)
                    if previous is not None:
                        previous.end = max(previous.end, end)
                        continue
                    if len(active) >= 4:
                        continue
                    new = copy.copy(n)
                    new.start = t
                    new.end = end
                    out.append(new)
                    active[n.pitch] = new
        inst.notes = sorted([n for n in out if n.end > n.start], key=lambda n: (n.start, n.pitch))
        report[inst.name] = {
            "before": len(notes),
            "after": len(inst.notes),
            "kick_follow": bool(bass and kicks),
        }

    # Sincroniza o bumbo da bateria para bater junto com o ataque do baixo quando próximos
    # 'eu queria que o bumbo batesse junto com o baixo(mas n toda vez tmb) so pra alinhar a bateria ao todo'
    if drum_inst and final_bass_starts:
        aligned_drum = []
        for n in drum_inst.notes:
            new_note = copy.copy(n)
            if n.pitch in (35, 36):
                close = [b for b in final_bass_starts if abs(n.start - b) <= 0.085]
                if close:
                    new_start = close[0]
                    new_note.start = new_start
                    new_note.end = new_start + max(0.05, n.end - n.start)
            aligned_drum.append(new_note)
        drum_inst.notes = sorted(aligned_drum, key=lambda n: (n.start, n.pitch))

    return report
