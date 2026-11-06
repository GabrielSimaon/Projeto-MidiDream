"""Arranjo simplificado: baixo no bumbo e acordes em colcheias."""

import copy
import numpy as np


def lock_rhythm(instruments, bpm, grid_times=None):
    """Seleciona alturas detectadas; não preenche silêncios com notas novas."""
    # Uma semicolcheia dura 1/4 de batida (grade métrica de 16avos do BPM).
    step = 60 / float(bpm) / 4.0
    anchor = 0.0

    def snap(t):
        return max(0.0, round(t / step) * step)

    # No padrão General MIDI, 35 e 36 são os dois sons de bumbo.
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
    for inst in instruments:
        name = inst.name.casefold()
        bass = "bass" in name or "baixo" in name
        guitar = any(x in name for x in ("guitar", "viol", "steel"))
        if inst.is_drum or not (bass or guitar):
            continue
        notes = sorted(inst.notes, key=lambda n: (n.start, n.pitch))
        out = []
        if bass:
            # O baixo só toca nos ataques do bumbo; sem bateria, usa a grade.
            attacks = kicks if kicks else sorted({snap(n.start) for n in notes})
            previous = None
            for t in attacks:
                # Só escolhe uma altura já detectada perto do ataque: não inventa notas.
                options = [
                    n
                    for n in notes
                    if n.start <= t + (0.075 if kicks else step / 2 + 0.001) and n.end >= t + 0.035
                ]
                if not options:
                    continue
                # A pontuação favorece intensidade, proximidade temporal e continuidade da linha.
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
                # Encerra a nota anterior para manter o baixo monofônico.
                if out:
                    out[-1].end = min(out[-1].end, t)
                out.append(new)
                previous = n.pitch
            if out:
                # Sustentação contínua de groove do baixo (alongamento musical de notas)
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
        else:
            # Agrupa os ataques de guitarra na grade de semicolcheias (16avos)
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
                    # Limita a duração do ataque de guitarra para não embolar com a próxima batida
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
    return report
