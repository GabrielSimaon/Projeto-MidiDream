"""Faixas prontas precisam pular download e separação, inclusive com um arquivo."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from test_studio import fixture
from pipeline import run_pipeline, prepare_uploaded_stems


class StemInputTests(unittest.TestCase):
    def run_stems(self, names):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stems = root / "stems"
            stems.mkdir()
            for name in names:
                sf.write(stems / name, np.zeros(22050), 22050)

            def engine(command, **kwargs):
                self.assertEqual(Path(command[2]), stems)
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return "inferência simulada"

            with (
                patch("pipeline.download_youtube") as download,
                patch("pipeline.separate_stems") as separate,
                patch("pipeline.transcribe_words", return_value=[]) as words,
                patch("pipeline._run", side_effect=engine),
            ):
                result = run_pipeline(root / "job", uploaded_stems_dir=stems)
                separate.assert_not_called()
                download.assert_not_called()
                self.assertTrue(result["archive"].is_file())
                self.assertEqual(result["duration"], 1.0)
                self.assertEqual(result["stems_dir"], stems)
                if not any("vocal" in name for name in names):
                    words.assert_not_called()
                    self.assertIn("Sem faixa de voz", result["transcription_warning"])

    def test_multiple_stems_without_full_song(self):
        self.run_stems(["vocals.wav", "bass.wav", "drums.wav"])

    def test_single_isolated_track_is_allowed(self):
        self.run_stems(["bass.wav"])

    def test_same_instrument_tracks_keep_alignment_and_longest_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for i, seconds in enumerate((1.0, 1.5)):
                samples = np.zeros(int(22050 * seconds))
                samples[4410:] = 0.2 * np.sin(
                    2 * np.pi * (220 + i * 110) * np.arange(len(samples) - 4410) / 22050
                )
                path = root / f"guitar_{i}.wav"
                sf.write(path, samples, 22050)
                paths.append(path)
            output = prepare_uploaded_stems(paths, root / "ready")
            signal, rate = sf.read(output / "guitar.wav")
            self.assertAlmostEqual(len(signal) / rate, 1.5, places=3)
            self.assertLess(np.max(np.abs(signal[: int(rate * 0.1)])), 1e-5)
            self.assertGreater(np.max(np.abs(signal[int(rate * 0.3) : int(rate * 0.5)])), 0.01)

    def test_unknown_names_are_rejected_before_separation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stems = root / "stems"
            stems.mkdir()
            sf.write(stems / "desconhecido.wav", np.zeros(22050), 22050)
            with patch("pipeline.separate_stems") as separate:
                with self.assertRaises(ValueError):
                    run_pipeline(root / "job", uploaded_stems_dir=stems)
                separate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
