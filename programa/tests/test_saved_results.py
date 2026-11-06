"""Recuperação sem recomputar IA e isolamento do processo de reconhecimento."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from test_studio import fixture
from saved_results import recover_result, list_saved_jobs, save_result_state
from pipeline import run_pipeline, transcribe_words


class SavedResultsTests(unittest.TestCase):
    def test_legacy_job_recovers_without_state_or_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job_old"
            midi = job / "resultados/midi"
            midi.mkdir(parents=True)
            fixture(midi / "completo.mid")
            (job / "resultados/palavras.json").write_text('[{"text":"Olá"}]')
            result = recover_result(job)
            self.assertEqual(result["detected_word_count"], 1)
            self.assertTrue(Path(result["archive"]).is_file())
            self.assertTrue(Path(result["events_path"]).is_file())
            self.assertEqual(list_saved_jobs(root), [job])

    def test_partial_midi_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            midi = job / "resultados/midi"
            midi.mkdir(parents=True)
            (midi / "completo.mid").write_bytes(b"broken")
            with self.assertRaises((OSError, EOFError)):
                recover_result(job)

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            midi = job / "resultados/midi"
            midi.mkdir(parents=True)
            fixture(midi / "completo.mid")
            save_result_state(
                {
                    "job_dir": job,
                    "source_audio": job / "song.wav",
                    "duration": 42,
                    "preview_seconds": 30,
                }
            )
            result = recover_result(job)
            self.assertEqual(result["duration"], 42)
            self.assertEqual(result["preview_seconds"], 30)

    def test_native_recognizer_failure_preserves_completed_midi(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            sf.write(audio, np.zeros(22050), 22050)
            job = root / "job_failure"

            def engine(command, **kwargs):
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return ""

            def fail(*args, **kwargs):
                self.assertEqual(
                    json.loads((job / "resultado_salvo.json").read_text())["saved_status"],
                    "midi_ready",
                )
                raise RuntimeError("worker terminou inesperadamente")

            with (
                patch("pipeline._run", side_effect=engine),
                patch("pipeline.transcribe_words", side_effect=fail),
            ):
                result = run_pipeline(job, audio_path=audio, separate=False)
            self.assertTrue(result["combined_mid"].is_file())
            self.assertIn("worker", result["transcription_warning"])
            self.assertTrue(Path(recover_result(job)["combined_mid"]).is_file())

    def test_interruption_can_recover_saved_midi(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            sf.write(audio, np.zeros(22050), 22050)
            job = root / "job_interrupted"

            def engine(command, **kwargs):
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return ""

            with (
                patch("pipeline._run", side_effect=engine),
                patch("pipeline.transcribe_words", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_pipeline(job, audio_path=audio, separate=False)
            recovered = recover_result(job)
            self.assertTrue(Path(recovered["archive"]).is_file())
            self.assertIn("não chegou a concluir", recovered["transcription_warning"])

    def test_recognition_uses_child_and_transmits_progress(self):
        def worker(command, **kwargs):
            self.assertEqual(Path(command[2]).name, "lyrics_worker.py")
            Path(command[command.index("--output") + 1]).write_text('[{"text":"Olá"}]')
            kwargs["on_output"]("LYRICS_PROGRESS 0.5")
            return ""

        progress = []
        with patch("pipeline._run", side_effect=worker):
            words = transcribe_words("voice.wav", fast=True, progress=progress.append)
        self.assertEqual(progress, [0.5])
        self.assertEqual(words[0]["text"], "Olá")


if __name__ == "__main__":
    unittest.main()
