"""Tons não viram caixa; bumbo e viradas mantêm sua identidade e grade."""

import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile as sf
import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drum_classifier import classify_body, TOM_NOTES
from audio2midi_core import transcribe_drums, build_grid
from midi_mix import mix_midi, validate_mix

RATE = 22050


def hit(frequency, snare=False, noise_gain=1):
    t = np.arange(int(RATE * 0.24)) / RATE
    tone = np.sin(2 * np.pi * frequency * t) * np.exp(-t / 0.09)
    if snare:
        rng = np.random.default_rng(7)
        noise = rng.normal(size=len(t))
        spectrum = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(len(t), 1 / RATE)
        spectrum[(freqs < 500) | (freqs > 4500)] = 0
        tone = 0.3 * tone + noise_gain * np.fft.irfft(spectrum, n=len(t)) * np.exp(-t / 0.06)
    return tone * np.minimum(t / 0.002, 1)


class DrumClassifierTests(unittest.TestCase):
    def test_tom_registers_and_snare_with_resonant_body(self):
        for frequency, pitch in [(90, 41), (115, 43), (145, 45), (180, 47), (230, 48), (300, 50)]:
            with self.subTest(frequency=frequency):
                self.assertEqual(classify_body(hit(frequency), RATE, 0)[0], pitch)
        pitch, sub, noise = classify_body(hit(180, snare=True), RATE, 0)
        self.assertIsNone(pitch)
        self.assertTrue(noise)
        # A esteira atenuada pelo demixing ainda deve distinguir a caixa do tom.
        self.assertTrue(classify_body(hit(180, snare=True, noise_gain=0.08), RATE, 0)[2])
        self.assertIsNone(classify_body(hit(55), RATE, 0)[0])
        self.assertIsNone(classify_body(np.zeros(500), RATE, 0)[0])

    def test_audio_fill_is_toms_snare_remains_snare_and_grid_is_preserved(self):
        audio = np.zeros(RATE * 3)
        sequence = [
            (0.5, 180, True),
            (1, 300, False),
            (1.25, 180, False),
            (1.5, 115, False),
            (2, 55, False),
        ]
        for time, frequency, snare in sequence:
            sample = hit(frequency, snare)
            pos = int(time * RATE)
            audio[pos : pos + len(sample)] += sample
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drums.wav"
            sf.write(path, audio, RATE)
            for fast in (False, True):
                with self.subTest(fast=fast):
                    midi = transcribe_drums(path, 120, build_grid(3, 120), 16, fast)
                    notes = midi.instruments[0].notes
                    for time, frequency, snare in sequence:
                        pitches = {n.pitch for n in notes if abs(n.start - time) < 0.04}
                        if snare:
                            self.assertIn(38, pitches)
                            self.assertFalse(pitches & TOM_NOTES)
                        elif frequency > 80:
                            self.assertTrue(pitches & TOM_NOTES)
                            self.assertNotIn(38, pitches)
                            self.assertNotIn(36, pitches)
                        else:
                            self.assertIn(36, pitches)
                            self.assertFalse(pitches & TOM_NOTES)
                    self.assertTrue(
                        all(abs(n.start / 0.125 - round(n.start / 0.125)) < 1e-6 for n in notes)
                    )
                    self.assertEqual(sum(n.pitch in TOM_NOTES for n in notes), 3)
                    self.assertFalse(any(n.pitch == 42 for n in notes))

    def test_tom_volume_independent_of_snare_and_hihat(self):
        midi = mido.MidiFile()
        midi.tracks.append(
            mido.MidiTrack(
                [
                    mido.Message("note_on", channel=9, note=p, velocity=80)
                    for p in (38, 42, 41, 43, 45, 47, 48, 50)
                ]
            )
        )
        mix_midi(midi, dict(drums=100, snare=100, hihat=100, toms=50))
        notes = [n for n in midi.tracks[0] if n.type == "note_on"]
        self.assertEqual([n.velocity for n in notes], [80, 80, 40, 40, 40, 40, 40, 40])
        self.assertEqual(validate_mix({})["toms"], 100)
        with self.assertRaises(ValueError):
            validate_mix({"toms": float("nan")})


if __name__ == "__main__":
    unittest.main()
