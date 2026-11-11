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
    garantindo que todas as notas estejam no tempo exato do metrônomo e no BPM.
    
    Preserva a duração audível de cada nota (NoteOff nunca colide com NoteOn no tick 0),
    evitando que viradas rápidas de bateria ou notas curtas cortem no meio do tempo.
    Não altera o contrabaixo ('n mexa no baixo').
    """
    target = target or source
    src_stem = str(source).casefold()
    if "bass" in src_stem or "baixo" in src_stem:
        # Diretriz do usuário: 'O baixo esta cortando mas ele ta certo, n mexa no baixo.'
        return

    mf = mido.MidiFile(str(source), charset="latin1")
    ppq = mf.ticks_per_beat
    actual_step = max(1, round(step * (ppq / 960.0)))
    actual_drum_step = max(1, round(drum_step * (ppq / 960.0)))

    for track in mf.tracks:
        track_name = next((m.name for m in track if m.type == "track_name"), "").casefold()
        if "bass" in track_name or "baixo" in track_name:
            continue

        is_drum = any(getattr(m, "channel", None) == 9 for m in track) or "drum" in track_name or "bater" in track_name or "chimbal" in track_name
        s = actual_drum_step if is_drum else actual_step
        min_dur = actual_drum_step if is_drum else max(1, actual_step // 2)

        # Converte mensagens para timeline absoluta (tick, ordem_tipo, msg)
        abs_tick = 0
        timeline = []
        active_notes = {}  # (channel, note) -> target_on_tick

        for msg in track:
            abs_tick += msg.time
            is_on = (msg.type == "note_on" and msg.velocity > 0)
            is_off = (msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0))
            channel = getattr(msg, "channel", None)

            if is_on and channel is not None and channel != 0:
                target_tick = round(abs_tick / s) * s
                active_notes[(channel, msg.note)] = target_tick
                timeline.append((target_tick, 2, msg))
            elif is_off and channel is not None and channel != 0:
                on_target = active_notes.pop((channel, msg.note), None)
                target_tick = round(abs_tick / s) * s
                if on_target is not None:
                    target_tick = max(target_tick, on_target + min_dur)
                timeline.append((target_tick, 1, msg))
            else:
                target_tick = round(abs_tick / s) * s if msg.is_meta or hasattr(msg, "channel") else abs_tick
                timeline.append((target_tick, 0, msg))

        # Ordena timeline: primeiro tick; para o mesmo tick: controles(0), note_off(1), note_on(2)
        timeline.sort(key=lambda item: (item[0], item[1]))

        # Reconstrói deltas relativos
        new_track = []
        last_tick = 0
        for target_tick, _, msg in timeline:
            delta = max(0, target_tick - last_tick)
            new_track.append(msg.copy(time=delta))
            last_tick = target_tick

        track.clear()
        track.extend(new_track)

    mf.save(str(target))
