import sys
import unittest
from pathlib import Path
import numpy as np
import pretty_midi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_vocals import parse_crowd_ranges, filter_vocal_notes, periodic_support


def vocal(notes):
    midi = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(52)
    inst.notes = [pretty_midi.Note(90, pitch, start, end) for pitch, start, end in notes]
    midi.instruments.append(inst)
    return midi


class LiveVoiceTests(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(parse_crowd_ranges("01:20-01:28\n85-90;2:10-2:14"), [(80, 90), (130, 134)])
        for text in ["1-0", "nan-5", "0-inf", "1:80-2:00", "-1-5", "invalid"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_crowd_ranges(text)

    def test_noise_rejected_and_tone_retained_without_shifting(self):
        sr = 16000
        t = np.arange(sr * 3) / sr
        y = 0.15 * np.sin(2 * np.pi * 440 * t)
        y[sr : sr * 2] = np.random.default_rng(7).normal(0, 0.15, sr)
        original = vocal([(69, 0.15, 0.65), (69, 1.15, 1.65), (69, 2.15, 2.65)])
        result, report = filter_vocal_notes(original, y, sr)
        notes = result.instruments[0].notes
        self.assertEqual([(n.start, n.end) for n in notes], [(0.15, 0.65), (2.15, 2.65)])
        self.assertEqual(report["notes_before"], 3)
        self.assertEqual(report["notes_after"], 2)
        self.assertEqual(len(original.instruments[0].notes), 3)

    def test_manual_cut_splits_note_exactly(self):
        result, report = filter_vocal_notes(
            vocal([(60, 0, 3)]), np.zeros(1), 16000, automatic=False, excluded=[(1, 2)]
        )
        self.assertEqual([(n.start, n.end) for n in result.instruments[0].notes], [(0, 1), (2, 3)])

    def test_manual_cut_cannot_be_closed_by_automatic_smoothing(self):
        sr = 16000
        y = 0.1 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr)
        result, _ = filter_vocal_notes(vocal([(69, 0.1, 0.9)]), y, sr, excluded=[(0.4, 0.44)])
        self.assertEqual(
            [(n.start, n.end) for n in result.instruments[0].notes], [(0.1, 0.4), (0.44, 0.9)]
        )

    def test_silence_and_empty_audio(self):
        for y in [np.zeros(16000), np.zeros(0)]:
            result, _ = filter_vocal_notes(vocal([(69, 0, 0.8)]), y, 16000)
            self.assertEqual(result.instruments[0].notes, [])

    def test_unison_is_not_claimed_to_be_identified_as_audience(self):
        sr = 16000
        y = 0.1 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr)
        support, _ = periodic_support(y, sr, [69])
        self.assertGreater(np.mean(support[69]), 0.9)


class LivePipelineTests(unittest.TestCase):
    def test_report_and_filter_settings_survive_cache(self):
        import tempfile, json
        from unittest.mock import patch
        import soundfile as sf
        from pipeline import run_pipeline
        from test_studio import fixture

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stems = root / "stems"
            stems.mkdir()
            sf.write(stems / "vocals.wav", np.zeros(16000), 16000)
            report = {"notes_before": 3, "notes_after": 2, "rejected_or_trimmed": []}

            def engine(command, **kwargs):
                self.assertEqual(command[command.index("--live-vocals") + 1], "strict")
                self.assertEqual(command[command.index("--crowd-ranges") + 1], "0.2-0.4")
                dest = Path(command[command.index("-o") + 1]) / "midi"
                dest.mkdir(parents=True)
                fixture(dest / "completo.mid")
                (dest / "relatorio_voz.json").write_text(json.dumps(report))
                return "ok"

            with patch("pipeline._run", side_effect=engine) as runner:
                for name in ("job1", "job2"):
                    result = run_pipeline(
                        root / name,
                        uploaded_stems_dir=stems,
                        cache_root=root / "cache",
                        recognize_lyrics=False,
                        live_vocals="strict",
                        crowd_ranges="0.2-0.4",
                    )
                    self.assertEqual(
                        json.loads((result["result_dir"] / "relatorio_voz.json").read_text()),
                        report,
                    )
                self.assertEqual(runner.call_count, 1)

    def test_express_rejected_before_inference(self):
        from pipeline import run_pipeline

        with self.assertRaisesRegex(ValueError, "Express"):
            run_pipeline("/unused", audio_path="song.mp3", speed="express", live_vocals="balanced")


class LiveReportSerializationTests(unittest.TestCase):
    def test_numpy_note_pitch_can_be_saved_as_json(self):
        import json

        midi = vocal([(np.int64(69), 0, 0.8)])
        result, report = filter_vocal_notes(midi, np.zeros(16000), 16000)
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        self.assertEqual(json.loads(encoded)["rejected_or_trimmed"][0]["pitch"], 69)
        self.assertEqual(result.instruments[0].notes, [])

    def test_trimmed_numpy_values_can_be_saved_as_json(self):
        import json

        midi = vocal([(np.int64(60), np.float64(0), np.float64(3))])
        _, report = filter_vocal_notes(midi, np.zeros(1), 16000, automatic=False, excluded=[(1, 2)])
        self.assertEqual(
            json.loads(json.dumps(report))["rejected_or_trimmed"][0]["kept"], [[0, 1], [2, 3]]
        )

    def test_report_failure_does_not_abort_midi(self):
        import tempfile
        from unittest.mock import patch
        import soundfile as sf
        import audio2midi_core as core

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "vocals.wav"
            output = root / "out"
            y = 0.1 * np.sin(2 * np.pi * 440 * np.arange(8000) / 16000)
            sf.write(source, y, 16000)
            midi = vocal([(np.int64(69), 0.1, 0.4)])
            with (
                patch(
                    "sys.argv",
                    [
                        "audio2midi_core.py",
                        str(source),
                        "-o",
                        str(output),
                        "--live-vocals",
                        "balanced",
                    ],
                ),
                patch.object(core, "prepare_wav", return_value=source),
                patch.object(core, "analyze_metronome", return_value=(120, np.array([]))),
                patch.object(core, "transcribe_vocal_melody", return_value=midi),
                patch.object(Path, "write_text", side_effect=OSError("relatório indisponível")),
            ):
                self.assertEqual(core.main(), 0)
            self.assertTrue((output / "midi/completo.mid").is_file())
