import copy, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np, pretty_midi, mido, soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rhythm_lock import lock_rhythm
from vocal_melody import notes_from_f0, transcribe_vocal_melody
from lyric_alignment import normalize_alignment
from note_cleanup import max_polyphony


def instrument(name, items, drums=False):
    inst = pretty_midi.Instrument(0, is_drum=drums, name=name)
    inst.notes = [pretty_midi.Note(vel, p, a, b) for p, a, b, vel in items]
    return inst


class RhythmMelodyTests(unittest.TestCase):
    def test_bass_uses_only_supported_kicks_and_is_monophonic(self):
        bass = instrument("Bass", [(40, 0.03, 0.8, 90), (52, 0.04, 0.4, 35), (43, 0.9, 1.3, 80)])
        drum = instrument(
            "Drums",
            [(36, 0, 0.1, 100), (36, 0.5, 0.6, 100), (36, 1, 1.1, 100), (36, 2, 2.1, 100)],
            True,
        )
        lock_rhythm([bass, drum], 120)
        self.assertEqual([n.start for n in bass.notes], [0, 0.5, 1])
        self.assertEqual([n.pitch for n in bass.notes], [40, 40, 43])
        self.assertEqual(max_polyphony(bass.notes), 1)
        self.assertEqual(len(drum.notes), 4)

    def test_bass_without_drums_follows_grid(self):
        bass = instrument("Bass", [(40, 0.24, 0.7, 90), (52, 0.25, 0.65, 20)])
        lock_rhythm([bass], 120)
        self.assertEqual([(n.start, n.pitch) for n in bass.notes], [(0.25, 40)])

    def test_guitar_quantized_four_notes_without_duplicate_attacks(self):
        guitar = instrument(
            "Steel-str.Gt",
            [(p, 0.12, 0.8, 70 - p % 5) for p in range(60, 70)] + [(60, 0.31, 1.0, 80)],
        )
        source = {n.pitch for n in guitar.notes}
        lock_rhythm([guitar], 120)
        self.assertTrue(
            all(abs(n.start / 0.25 - round(n.start / 0.25)) < 1e-7 for n in guitar.notes)
        )
        self.assertLessEqual(max_polyphony(guitar.notes), 4)
        self.assertTrue({n.pitch for n in guitar.notes} <= source)
        for p in source:
            ns = sorted([n for n in guitar.notes if n.pitch == p], key=lambda n: n.start)
            self.assertTrue(all(a.end <= b.start for a, b in zip(ns, ns[1:])))

    def test_vocal_and_lyrics_are_outside_rhythm_processing(self):
        vocal = instrument("Vocal Flute", [(60, 0.123, 0.387, 80)])
        before = copy.deepcopy(vocal.notes)
        self.assertEqual(lock_rhythm([vocal], 158), {})
        self.assertEqual(
            [(n.start, n.end) for n in vocal.notes], [(n.start, n.end) for n in before]
        )

    def test_contour_rejects_unvoiced_frames_without_chords(self):
        f0 = np.r_[np.ones(30) * 220, np.ones(10) * np.nan, np.ones(30) * 329.6276]
        voiced = np.isfinite(f0)
        prob = voiced.astype(float)
        rms = np.ones(70) * 0.1
        notes = notes_from_f0(f0, voiced, prob, rms, 16000, 160, 0.7)
        self.assertEqual([n.pitch for n in notes], [57, 64])
        self.assertEqual(max_polyphony(notes), 1)
        self.assertGreaterEqual(notes[1].start, 0.4)

    def test_actual_pyin_tones_and_silence(self):
        rate = 16000
        t = np.arange(rate * 0.7) / rate
        audio = np.r_[
            0.3 * np.sin(2 * np.pi * 220 * t),
            np.zeros(int(rate * 0.25)),
            0.3 * np.sin(2 * np.pi * 261.6256 * t),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "v.wav"
            sf.write(path, audio, rate)
            m = transcribe_vocal_melody(path)
        notes = m.instruments[0].notes
        self.assertEqual({n.pitch for n in notes}, {57, 60})
        self.assertEqual(max_polyphony(notes), 1)
        self.assertFalse(any(0.8 < n.start < 0.9 for n in notes))

    def test_alignment_does_not_assign_instrumental_pause_to_one_word(self):
        result = {
            "segments": [
                {
                    "words": [
                        {"word": "Pensei", "start": 88.0, "end": 97.7},
                        {"word": "em", "start": 97.7, "end": 97.9},
                    ]
                }
            ]
        }
        words = normalize_alignment(result, "pt")
        self.assertGreater(words[0]["start"], 97)
        self.assertLess(words[0]["end"] - words[0]["start"], 1.0)
        self.assertEqual([w["text"] for w in words], ["Pensei", "em"])

    def test_imported_lyrics_realign_against_voice_when_enabled(self):
        from test_studio import fixture
        from pipeline import run_pipeline
        from lyrics import read_timed_lyrics

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stems = root / "stems"
            stems.mkdir()
            sf.write(stems / "vocals.wav", np.zeros(16000 * 4), 16000)

            def engine(command, **kwargs):
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return "ok"

            words = [
                {"text": "Meu", "start": 1.0, "end": 1.3, "line": 0},
                {"text": "refrão", "start": 1.4, "end": 2.0, "line": 0},
            ]
            with (
                patch("pipeline._run", side_effect=engine),
                patch("pipeline.transcribe_words", return_value=words) as align,
            ):
                result = run_pipeline(
                    root / "job",
                    uploaded_stems_dir=stems,
                    recognize_lyrics=False,
                    timed_lyrics=[
                        {"seconds": 0.1, "text": "Meu "},
                        {"seconds": 3.0, "text": "refrão"},
                    ],
                    align_lyrics=True,
                )
            self.assertEqual(align.call_args.kwargs["alignment_text"], "Meu refrão")
            rows = read_timed_lyrics(result["karaoke_kar"])
            self.assertAlmostEqual(rows[0]["seconds"], 1.0, places=2)
            self.assertEqual(
                "".join(r["text"] for r in rows if r["text"] != "[QUEBRA]"), "Meu refrão"
            )


if __name__ == "__main__":
    unittest.main()
