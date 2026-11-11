"""Versão 4.4 — perfil fixo de máxima qualidade e fidelidade às notas originais."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pretty_midi
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audio2midi_core as core
from note_cleanup import remove_overtone_ghosts
from quality import fixed_settings


def note(pitch, start, end, velocity):
    return pretty_midi.Note(velocity, pitch, start, end)


class FixedProfileTests(unittest.TestCase):
    def test_profile_never_lowers_quality_or_rewrites_notes(self):
        for stems in (False, True):
            settings = fixed_settings(stems_provided=stems)
            self.assertEqual(settings["speed"], "quality")
            self.assertIn(settings["demucs_model"], ("htdemucs_ft", "htdemucs_6s"))
            self.assertEqual(settings["whisper_model"], "medium")
            self.assertEqual(settings["preview_seconds"], 0)
            self.assertFalse(settings["quick_pitch"])
            self.assertTrue(settings["accurate_lyrics"])
            self.assertTrue(settings["complete_instruments"])
            self.assertEqual(settings["separate"], not stems)
            # Tonality padrão
            self.assertIsNone(settings["tonality"])
            self.assertIn("harmonic_correction", settings)
            self.assertIn("rhythm_lock", settings)
            self.assertFalse(settings["chord_accompaniment"])
            self.assertIn(settings["grid"], (0, 16))
            self.assertIn(settings["accompaniment_filter"], ("balanced", "chords"))

    def test_profile_arguments_are_accepted_by_pipeline_signature(self):
        import inspect
        from pipeline import run_pipeline

        accepted = set(inspect.signature(run_pipeline).parameters)
        self.assertLessEqual(set(fixed_settings()), accepted)


class OvertoneGhostTests(unittest.TestCase):
    def test_weak_octave_at_same_attack_is_removed(self):
        notes = [note(48, 1.0, 1.5, 100), note(60, 1.01, 1.5, 40), note(67, 1.0, 1.5, 45)]
        kept, removed = remove_overtone_ghosts(notes)
        self.assertEqual([n.pitch for n in kept], [48])
        self.assertEqual(removed, 2)

    def test_real_octave_doubling_with_similar_strength_is_kept(self):
        notes = [note(48, 1.0, 1.5, 100), note(60, 1.0, 1.5, 90)]
        kept, removed = remove_overtone_ghosts(notes)
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, 0)

    def test_octave_played_later_or_alone_is_kept(self):
        notes = [note(48, 1.0, 1.4, 100), note(60, 1.6, 2.0, 40), note(72, 3.0, 3.4, 30)]
        kept, removed = remove_overtone_ghosts(notes)
        self.assertEqual(len(kept), 3)
        self.assertEqual(removed, 0)

    def test_other_intervals_are_never_touched(self):
        notes = [note(48, 1.0, 1.5, 100), note(52, 1.0, 1.5, 30), note(55, 1.0, 1.5, 30)]
        kept, removed = remove_overtone_ghosts(notes)
        self.assertEqual(len(kept), 3)

    def test_post_process_applies_filter_only_to_polyphonic_stems(self):
        def make():
            midi = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(0)
            inst.notes = [note(48, 1.0, 1.6, 100), note(60, 1.0, 1.6, 40)]
            midi.instruments.append(inst)
            return midi

        grid = np.array([])
        piano = core.post_process_notes(make(), "piano", 120, grid, grid=0)
        vocals = core.post_process_notes(make(), "vocals", 120, grid, grid=0)
        self.assertEqual([n.pitch for n in piano.instruments[0].notes], [48])
        self.assertEqual(len(vocals.instruments[0].notes), 2)


class BeatGridTests(unittest.TestCase):
    def click_track(self, folder, bpm, offset, seconds=60):
        sr = 22050
        y = np.zeros(int(sr * seconds))
        period, t, k = 60 / bpm, offset, 0
        while t < seconds - 0.3:
            size, start = int(0.08 * sr), int(t * sr)
            tone = np.sin(2 * np.pi * (60 if k % 2 == 0 else 180) * np.arange(size) / sr)
            y[start : start + size] += (1.0 if k % 4 == 0 else 0.6) * tone * np.exp(
                -np.arange(size) / (0.02 * sr)
            )
            t += period
            k += 1
        path = Path(folder) / "click.wav"
        sf.write(path, y, sr)
        return path

    def test_measures_real_tempo_and_phase_near_typed_bpm(self):
        with tempfile.TemporaryDirectory() as folder:
            for true_bpm, typed, offset in ((157.6, 158, 0.37), (100.3, 100, 0.21), (120, 120, 0)):
                path = self.click_track(folder, true_bpm, offset)
                measured = core.estimate_beat_grid(path, typed)
                self.assertIsNotNone(measured)
                bpm, phase = measured
                self.assertAlmostEqual(bpm, true_bpm, delta=0.05)
                period = 60 / true_bpm
                error = ((phase - offset + period / 2) % period) - period / 2
                self.assertLess(abs(error), 0.008)

    def test_noise_has_no_pulse_so_grid_falls_back_to_typed_bpm(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "noise.wav"
            sf.write(path, 0.05 * np.random.default_rng(1).standard_normal(22050 * 30), 22050)
            self.assertIsNone(core.estimate_beat_grid(path, 120))

    def test_analyze_metronome_keeps_typed_bpm_but_grid_follows_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.click_track(folder, 157.6, 0.37)
            bpm, grid = core.analyze_metronome(
                None, 4, 60, bpm_hint=158.0, reference_path=path
            )
            self.assertEqual(bpm, 158.0)
            step = float(np.median(np.diff(grid)))
            self.assertAlmostEqual(step, 60 / 157.6 / 4, delta=0.0005)
            # Algum ponto da grade cai sobre cada clique, mesmo no fim da música.
            beat = 60 / 157.6
            for index in (0, 50, 90):
                click = 0.37 + index * beat
                self.assertLess(float(np.min(np.abs(grid - click))), 0.01)


class SyllableTimingTests(unittest.TestCase):
    def test_longer_syllables_get_more_time(self):
        from lyrics import build_lyric_events

        events, _, _ = build_lyric_events(
            [{"text": "inconstitucionalmente", "start": 0.0, "end": 2.0}], "", 3.0, "pt"
        )
        times = [t for t, text in events if text.strip() and text != "\r\n"]
        self.assertGreater(len(times), 3)
        gaps = np.diff(times)
        self.assertGreater(len(set(np.round(gaps, 3))), 1)
        self.assertAlmostEqual(times[0], 0.0, places=3)
        self.assertLess(times[-1], 2.0)


if __name__ == "__main__":
    unittest.main()
