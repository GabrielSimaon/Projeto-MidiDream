import sys
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf

from instrument_detection import decide_presence
import instrument_worker as worker


class AccompanimentPreservationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source, self.output = self.root / "input", self.root / "output"
        self.source.mkdir()
        self.t = np.arange(32000) / 32000

    def tearDown(self):
        self.folder.cleanup()

    def audio(self, kind, frequency):
        data = (0.1 * np.sin(2 * np.pi * frequency * self.t)).astype(np.float32)
        sf.write(self.source / (kind + ".wav"), data, 32000, subtype="FLOAT")
        return data

    def run_worker(self, detection, error=None):
        with patch("sys.argv", ["worker", "--source", str(self.source), "--output", str(self.output), "--models", str(self.root / "models")]), \
             patch.object(worker, "detect", return_value=detection, side_effect=error) as detect, \
             patch.object(worker, "download_weights") as download, \
             patch.object(worker, "load_backend") as load:
            worker.main()
        download.assert_not_called()
        load.assert_not_called()
        return json.loads((self.output / "instrumentos.json").read_text()), detect

    def test_known_piano_and_guitar_survive_without_detector_votes(self):
        piano = self.audio("piano", 261.63)
        guitar = self.audio("guitar", 196)
        bass = self.audio("bass", 82.4)
        before = (self.source / "bass.wav").read_bytes()
        report, detect = self.run_worker(decide_presence([]))
        detect.assert_not_called()
        self.assertEqual(set(report["preserved"]), {"piano", "guitar"})
        self.assertFalse(report["fallback"])
        self.assertIsNone(report["presence"]["piano"]["score"])
        np.testing.assert_allclose(sf.read(self.output / "piano.wav")[0], piano, atol=1e-7)
        np.testing.assert_allclose(sf.read(self.output / "guitar.wav")[0], guitar, atol=1e-7)
        self.assertEqual((self.output / "bass.wav").read_bytes(), before)
        self.assertFalse((self.output / "accordion.wav").exists())

    def test_uncertain_mix_remains_in_transcription_folder(self):
        original = self.audio("other", 440)
        report, detect = self.run_worker(decide_presence([{}]))
        self.assertEqual(report["exported"], ["other"])
        self.assertTrue(report["fallback"])
        np.testing.assert_allclose(sf.read(self.output / "other.wav")[0], original, atol=1e-7)
        self.assertFalse((self.output / "electric_guitar.wav").exists())
        self.assertFalse((self.output / "accordion.wav").exists())

    def test_only_unknown_audio_reaches_second_detector(self):
        self.audio("piano", 261.63)
        unknown = self.audio("other", 440)
        report, detect = self.run_worker(decide_presence([{}]))
        np.testing.assert_allclose(detect.call_args.args[0], unknown, atol=1e-7)
        self.assertEqual(set(report["exported"]), {"piano", "other"})
        self.assertTrue((self.output / "piano.wav").exists())

    def test_detector_failure_does_not_remove_known_or_unknown_accompaniment(self):
        self.audio("piano", 261.63)
        self.audio("other", 440)
        report, _ = self.run_worker(decide_presence([]), RuntimeError("modelo indisponível"))
        self.assertEqual(set(report["exported"]), {"piano", "other"})
        self.assertTrue(report["errors"])
        self.assertTrue((self.output / "piano.wav").exists())
        self.assertTrue((self.output / "other.wav").exists())


if __name__ == "__main__":
    unittest.main()
