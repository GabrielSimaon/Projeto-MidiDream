"""Testes de resultado reutilizável, avanço real e modo de transcrição rápida."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from test_studio import fixture
from performance import cache_key, read_cache, save_cache
from pipeline import _run, worker_progress, separate_stems, run_pipeline
from audio2midi_core import snap_time_to_grid, transcribe_monophonic


class PerformanceTests(unittest.TestCase):
    def test_grid_search_preserves_nearest_and_ties(self):
        grid = np.arange(0, 600, 0.125)
        for t in [-1, 0, 0.0625, 1.043, 100, 700]:
            self.assertEqual(snap_time_to_grid(t, grid, 0.125), grid[np.argmin(np.abs(grid - t))])

    def test_streaming_output_calls_progress_before_exit(self):
        lines = []
        output = _run(
            [sys.executable, "-c", 'print("[progress] 1/4 bass", flush=True)'],
            on_output=lines.append,
        )
        self.assertIn("[progress] 1/4 bass", lines)
        self.assertIn("bass", output)

    def test_progress_only_uses_reported_values(self):
        events = []
        callback = lambda stage, detail: events.append((stage, detail))
        worker_progress("[progress] 2/4 bass", "midi", callback)
        worker_progress(" 33%|xxx| 12/36 [00:10, 1.1seconds/s]", "demucs", callback)
        worker_progress(" 17%|xxx| 17M/100M [00:01, 10MB/s]", "demucs", callback)
        worker_progress("arbitrary text", "midi", callback)
        self.assertEqual([event[0] for event in events], ["midi", "demucs", "modelo"])
        self.assertTrue(events[0][1].startswith("50%"))
        self.assertTrue(events[1][1].startswith("33%"))
        self.assertTrue(events[2][1].startswith("17%"))

    def test_cache_uses_content_and_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "a.wav"
            file.write_bytes(b"audio")
            key = cache_key([("mix", file)], {"speed": "fast"})
            self.assertNotEqual(key, cache_key([("mix", file)], {"speed": "quality"}))
            save_cache(root / "cache", key, [file])
            cached = read_cache(root / "cache", key, ["a.wav"])
            self.assertIsNotNone(cached)
            (cached / "a.wav").write_bytes(b"corrupt result")
            self.assertIsNone(read_cache(root / "cache", key, ["a.wav"]))
            save_cache(root / "cache", key, [file])
            self.assertIsNotNone(read_cache(root / "cache", key, ["a.wav"]))
            file.write_bytes(b"other audio")
            self.assertNotEqual(key, cache_key([("mix", file)], {"speed": "fast"}))

    def test_fast_separation_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fake(command, **kwargs):
                target = root / "stems" / "vocals.wav"
                target.parent.mkdir(exist_ok=True)
                target.touch()
                self.assertEqual(command[command.index("-n") + 1], "hdemucs_mmi")
                self.assertEqual(command[command.index("--shifts") + 1], "0")
                self.assertEqual(command[command.index("--overlap") + 1], "0.05")
                return ""

            with patch("pipeline._run", side_effect=fake):
                separate_stems(root / "song.wav", root / "stems", speed="fast")

    def test_fast_pitch_on_known_voice_and_bass_tones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stem, freq, expected in [("vocals", 440, 69), ("bass", 55, 33)]:
                sr = 16000
                y = np.r_[
                    np.zeros(sr // 4),
                    0.2 * np.sin(2 * np.pi * freq * np.arange(sr) / sr),
                    np.zeros(sr // 4),
                ]
                file = root / (stem + ".wav")
                sf.write(file, y, sr)
                midi = transcribe_monophonic(file, stem)
                notes = midi.instruments[0].notes
                self.assertTrue(
                    any(n.pitch == expected and n.end - n.start > 0.5 for n in notes), notes
                )
                self.assertTrue(all(n.end <= len(y) / sr for n in notes))

    def test_midi_cache_skips_second_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            sf.write(audio, np.zeros(22050), 22050)

            def engine(command, **kwargs):
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return "inferencia simulada"

            with patch("pipeline._run", side_effect=engine) as run:
                for i in range(2):
                    result = run_pipeline(
                        root / f"job{i}",
                        audio_path=audio,
                        separate=False,
                        recognize_lyrics=False,
                        cache_root=root / "cache",
                    )
                    self.assertTrue(result["archive"].is_file())
                run.assert_called_once()
            self.assertIn("cache", result["core_log"])

    def test_lyrics_cache_skips_second_recognition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            sf.write(audio, np.zeros(22050), 22050)

            def engine(command, **kwargs):
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return ""

            words = [{"text": "Olá", "start": 0.1, "end": 0.6}]
            with (
                patch("pipeline._run", side_effect=engine),
                patch("pipeline.transcribe_words", return_value=words) as recognize,
            ):
                for i in range(2):
                    result = run_pipeline(
                        root / f"job{i}",
                        audio_path=audio,
                        separate=False,
                        cache_root=root / "cache",
                    )
                    self.assertIsNotNone(result["karaoke_kar"])
                recognize.assert_called_once()

    def test_stem_cache_skips_second_separation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            sf.write(audio, np.zeros(22050), 22050)

            def separate(source, target, **kwargs):
                target.mkdir(parents=True)
                for name in ("bass", "vocals", "other", "drums"):
                    sf.write(target / (name + ".wav"), np.zeros(22050), 22050)
                return target

            def engine(command, **kwargs):
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return ""

            with (
                patch("pipeline.separate_stems", side_effect=separate) as split,
                patch("pipeline._run", side_effect=engine),
            ):
                for i in range(2):
                    run_pipeline(
                        root / f"job{i}",
                        audio_path=audio,
                        speed="fast",
                        recognize_lyrics=False,
                        cache_root=root / "cache",
                    )
                split.assert_called_once()

    def test_preview_keeps_original_and_limits_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            sf.write(audio, np.zeros(22050 * 31), 22050)
            real_run = _run

            def command(args, **kwargs):
                if args[0] == "ffmpeg":
                    return real_run(args, **kwargs)
                target = Path(args[args.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return ""

            with patch("pipeline._run", side_effect=command):
                result = run_pipeline(
                    root / "job",
                    audio_path=audio,
                    separate=False,
                    preview_seconds=30,
                    recognize_lyrics=False,
                )
            self.assertAlmostEqual(result["duration"], 30, places=2)
            self.assertAlmostEqual(sf.info(audio).duration, 31, places=2)
            self.assertNotEqual(result["source_audio"], audio)

    def test_express_skips_separation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            sf.write(audio, np.zeros(22050), 22050)

            def engine(command, **kwargs):
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return ""

            with (
                patch("pipeline._run", side_effect=engine),
                patch("pipeline.separate_stems") as separate,
            ):
                run_pipeline(
                    root / "job", audio_path=audio, speed="express", recognize_lyrics=False
                )
                separate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
