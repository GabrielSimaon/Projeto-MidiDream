import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from music_guide import validate_required_inputs
from pipeline import run_pipeline


class RequiredGuideTests(unittest.TestCase):
    def test_missing_and_invalid_bpm(self):
        for bpm in (-1, 29.9, 300.1, float("nan"), float("inf"), "abc"):
            with self.subTest(bpm=bpm), self.assertRaises(ValueError):
                validate_required_inputs("[C]Minha música", bpm)
        self.assertIsNone(validate_required_inputs("[C]Minha música", None))
        self.assertEqual(validate_required_inputs("[C]Minha música", 158.5), 158.5)

    def test_requires_both_lyrics_and_valid_chords(self):
        for guide in (
            "",
            "Minha letra sem cifras",
            "[C] [Am]",
            "[H]Letra",
            "[C Letra",
            "[C]Letra]",
            "[00:nan C]Letra",
        ):
            with self.subTest(guide=guide), self.assertRaises(ValueError):
                validate_required_inputs(guide, 120)
        self.assertEqual(validate_required_inputs("[C]Minha [00:12.5 Am]música", 120), 120)

    def test_required_inputs_fail_before_job_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "job"
            with patch("pipeline.download_youtube") as download:
                with self.assertRaises(ValueError):
                    run_pipeline(
                        target,
                        youtube_url="https://youtube.com/watch?v=x",
                        require_guide=True,
                        bpm_override=120,
                    )
                download.assert_not_called()
            self.assertFalse(target.exists())

    def test_reference_cannot_supply_required_bpm(self):
        with patch("pipeline.read_reference") as reference:
            with self.assertRaises(ValueError):
                run_pipeline(
                    "unused",
                    manual_lyrics="[C]Minha música",
                    musical_reference="unused.mid",
                    require_guide=True,
                )
            reference.assert_not_called()

    def test_explicit_bpm_reaches_engine_with_reference(self):
        import numpy as np, soundfile as sf
        from test_studio import fixture

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "vocals.wav"
            sf.write(audio, np.zeros(16000 * 4), 16000)
            reference = root / "reference.mid"
            fixture(reference)

            def engine(command, **kwargs):
                self.assertEqual(float(command[command.index("--bpm") + 1]), 142.0)
                dest = Path(command[command.index("-o") + 1]) / "midi"
                dest.mkdir(parents=True)
                fixture(dest / "completo.mid")
                return "ok"

            with patch("pipeline._run", side_effect=engine):
                run_pipeline(
                    root / "job",
                    audio_path=audio,
                    separate=False,
                    recognize_lyrics=False,
                    manual_lyrics="[C]Minha música",
                    bpm_override=142,
                    require_guide=True,
                    musical_reference=reference,
                )


if __name__ == "__main__":
    unittest.main()
