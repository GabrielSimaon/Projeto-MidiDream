"""Casos musicais: crash/chimbal simultâneos e viradas sem compressão temporal."""

import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np, mido, pretty_midi, soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drum_timing import quantize_drums, split_hihat
from audio2midi_core import transcribe_drums, build_grid
from lyrics import remap_channels


def midi_with(items):
    midi = pretty_midi.PrettyMIDI(initial_tempo=120, resolution=960)
    inst = pretty_midi.Instrument(0, is_drum=True, name="Drums")
    inst.notes = [pretty_midi.Note(v, p, a, a + d) for p, a, d, v in items]
    midi.instruments = [inst]
    return midi


def synthetic_kit():
    rate = 22050
    audio = np.zeros(rate * 2)
    rng = np.random.default_rng(13)

    def band_noise(seconds, lo, hi):
        n = int(rate * seconds)
        noise = rng.normal(size=n)
        spectrum = np.fft.rfft(noise)
        frequencies = np.fft.rfftfreq(n, 1 / rate)
        spectrum[(frequencies < lo) | (frequencies > hi)] = 0
        return np.fft.irfft(spectrum, n=n)

    for onset in (0.3, 0.5, 0.7, 0.9, 1.1):
        hit = band_noise(0.025, 6500, 10000) * np.hanning(int(rate * 0.025))
        index = int(onset * rate)
        audio[index : index + len(hit)] += hit * 0.6
    crash = band_noise(0.55, 2600, 5200)
    times = np.arange(len(crash)) / rate
    crash *= 0.7 * (1 - np.exp(-times / 0.008)) * np.exp(-times / 0.4)
    index = int(0.5 * rate)
    audio[index : index + len(crash)] += crash
    return audio, rate


class DrumTimingTests(unittest.TestCase):
    def test_crash_does_not_replace_simultaneous_hihat(self):
        midi = midi_with([(42, 0.5, 0.055, 80), (49, 0.5, 0.3, 110), (42, 0.75, 0.055, 80)])
        quantize_drums(midi, 120, build_grid(2, 120), 16)
        split_hihat(midi)
        self.assertEqual(len(midi.instruments), 2)
        hat = next(i for i in midi.instruments if "Chimbal" in i.name)
        self.assertEqual(len(hat.notes), 2)
        self.assertAlmostEqual(hat.notes[0].end, 0.555)
        self.assertTrue(all(i.is_drum for i in midi.instruments))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "kit.mid"
            midi.write(str(path))
            remap_channels(path)
            output = mido.MidiFile(path)
        channels = {m.channel for t in output.tracks for m in t if m.type == "note_on"}
        self.assertEqual(channels, {9})

    def test_fill_keeps_beats_and_uses_strongest_duplicate(self):
        midi = midi_with(
            [
                (38, 1.01, 0.1, 50),
                (38, 1.03, 0.1, 100),
                (38, 1.13, 0.1, 80),
                (38, 1.26, 0.1, 80),
                (38, 1.38, 0.1, 90),
            ]
        )
        quantize_drums(midi, 120, build_grid(3, 120), 16)
        notes = midi.instruments[0].notes
        self.assertEqual([n.start for n in notes], [1.0, 1.125, 1.25, 1.375])
        self.assertEqual(notes[0].velocity, 100)
        self.assertTrue(all(a.end <= b.start for a, b in zip(notes, notes[1:])))

    def test_grid_phase_and_fast_repeated_hits(self):
        midi = midi_with([(42, 0.14, 0.15, 80), (42, 0.21, 0.15, 90)])
        quantize_drums(midi, 240, build_grid(1, 240, sub_div=4, anchor=0.02), 16)
        notes = midi.instruments[0].notes
        self.assertAlmostEqual(notes[0].start, 0.145)
        self.assertAlmostEqual(notes[1].start, 0.2075)
        self.assertEqual(notes[0].end, notes[1].start)

    def test_audio_detects_hihat_under_crash_tail(self):
        audio, rate = synthetic_kit()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "kit.wav"
            sf.write(path, audio, rate)
            midi = transcribe_drums(path, 120, build_grid(2, 120), grid=16)
        notes = [n for i in midi.instruments for n in i.notes]
        hats = [n for n in notes if n.pitch == 42]
        crashes = [n for n in notes if n.pitch == 49]
        self.assertGreaterEqual(len(hats), 4)
        self.assertTrue(any(abs(n.start - 0.5) < 0.04 for n in crashes))
        self.assertTrue(any(abs(n.start - 0.5) < 0.04 for n in hats))
        self.assertTrue(any(0.65 <= n.start <= 1.1 for n in hats))

    def test_engine_exports_hihat_separately_even_with_global_grid_off(self):
        import audio2midi_core as core

        audio, rate = synthetic_kit()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "drums.wav"
            out = root / "output"
            sf.write(path, audio, rate)
            with (
                patch(
                    "sys.argv", ["core", str(path), "-o", str(out), "--bpm", "120", "--grid", "0"]
                ),
                patch.object(core, "prepare_wav", return_value=path),
            ):
                self.assertEqual(core.main(), 0)
            hat = pretty_midi.PrettyMIDI(str(out / "midi/hihat.mid"))
            self.assertTrue(all(n.pitch in (42, 44, 46) for i in hat.instruments for n in i.notes))
            combined = pretty_midi.PrettyMIDI(str(out / "midi/completo.mid"))
            self.assertTrue(any("Chimbal" in i.name for i in combined.instruments))
            self.assertTrue(
                all(
                    abs(n.start / 0.125 - round(n.start / 0.125)) < 0.02
                    for i in combined.instruments
                    for n in i.notes
                )
            )


if __name__ == "__main__":
    unittest.main()
