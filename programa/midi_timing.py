"""Conversão temporal com mapa de andamento completo, sem acumular arredondamentos."""

from bisect import bisect_right

import mido


class TempoMap:
    def __init__(self, midi):
        if midi.ticks_per_beat <= 0:
            raise ValueError("MIDI com divisão SMPTE não é suportado.")
        self.ppq = midi.ticks_per_beat
        self.ticks, self.seconds, self.tempos = [0], [0.0], [500_000]
        tick, seconds, tempo = 0, 0.0, 500_000
        for msg in mido.merge_tracks(midi.tracks):
            seconds += mido.tick2second(msg.time, self.ppq, tempo)
            tick += msg.time
            if msg.type == "set_tempo":
                tempo = msg.tempo
                if tick == self.ticks[-1]:
                    self.tempos[-1] = tempo
                else:
                    self.ticks.append(tick)
                    self.seconds.append(seconds)
                    self.tempos.append(tempo)

    def ticks_to_seconds(self, ticks):
        """Converte uma posição absoluta, respeitando mudanças de andamento."""
        ticks = max(0, int(ticks))
        index = bisect_right(self.ticks, ticks) - 1
        return self.seconds[index] + mido.tick2second(
            ticks - self.ticks[index], self.ppq, self.tempos[index]
        )

    def seconds_to_ticks(self, seconds):
        seconds = max(0.0, float(seconds))
        i = bisect_right(self.seconds, seconds) - 1
        return round(
            self.ticks[i] + mido.second2tick(seconds - self.seconds[i], self.ppq, self.tempos[i])
        )


def rescale_ppq(midi, ppq=480):
    """Arredonda posições absolutas; escalar deltas individualmente causa deriva."""
    if ppq not in (480, 960) or midi.ticks_per_beat <= 0:
        raise ValueError("Use PPQ 480 ou 960 e um MIDI com divisão musical.")
    ratio = ppq / midi.ticks_per_beat
    for track in midi.tracks:
        absolute = previous = 0
        for msg in track:
            absolute += msg.time
            tick = round(absolute * ratio)
            msg.time, previous = tick - previous, tick
    midi.ticks_per_beat = ppq
    midi.type = 1
    return midi


def make_instrumental(source, target):
    """Remove pistas com notas do canal 2 (melodia vocal), preservando o condutor."""
    midi = mido.MidiFile(source, charset="latin1")
    midi.tracks = [
        t for t in midi.tracks if not any(m.type == "note_on" and m.channel == 1 for m in t)
    ]
    midi.save(target)


def quantize_midi_file_ticks(source, target=None, step=240, drum_step=120):
    """Crava cada evento de nota no múltiplo exato de ticks da grade métrica.
    
    Elimina qualquer desvio residual de ponto flutuante acumulado por conversões de tempo,
    garantindo que 100% das notas estejam no tempo exato do metrônomo e no BPM.
    """
    target = target or source
    mf = mido.MidiFile(str(source), charset="latin1")
    ppq = mf.ticks_per_beat
    actual_step = max(1, round(step * (ppq / 960.0)))
    actual_drum_step = max(1, round(drum_step * (ppq / 960.0)))

    for track in mf.tracks:
        abs_tick = 0
        last_tick = 0
        new_track = []
        is_drum = any(getattr(m, "channel", 0) == 9 for m in track)
        s = actual_drum_step if is_drum else actual_step

        for msg in track:
            abs_tick += msg.time
            target_tick = round(abs_tick / s) * s
            delta = max(0, target_tick - last_tick)
            new_track.append(msg.copy(time=delta))
            last_tick = target_tick
        track.clear()
        track.extend(new_track)
    mf.save(str(target))
