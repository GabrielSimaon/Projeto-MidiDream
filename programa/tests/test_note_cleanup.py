import sys, copy, unittest, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pretty_midi
import soundfile as sf
from note_cleanup import clean_notes, max_polyphony


def make(notes, drums=False):
    midi = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(33, is_drum=drums)
    inst.notes = [pretty_midi.Note(vel, p, start, end) for p, start, end, vel in notes]
    midi.instruments.append(inst)
    return midi


class CleanupTests(unittest.TestCase):
    def test_bass_harmonic_removed_without_changing_fundamental_pitch_or_timing(self):
        midi = make([(40, 0, 0.4, 90), (52, 0, 0.4, 65), (43, 0.5, 0.9, 90), (55, 0.5, 0.9, 65)])
        out, report = clean_notes(midi, "bass")
        self.assertEqual(
            [(n.pitch, n.start, n.end) for n in out.instruments[0].notes],
            [(40, 0, 0.4), (43, 0.5, 0.9)],
        )
        self.assertEqual(report["max_polyphony_after"], 1)
        self.assertEqual(len(midi.instruments[0].notes), 4)

    def test_guitar_preserves_six_note_chord_and_discards_weak_overlapping_extras(self):
        chord = [(p, 0, 0.4, 90) for p in (40, 47, 52, 55, 59, 64)]
        midi = make(chord + [(p, 0, 0.4, 14) for p in (41, 48, 53, 56, 60, 65)])
        out, _ = clean_notes(midi, "guitar")
        self.assertEqual({n.pitch for n in out.instruments[0].notes}, {40, 47, 52, 55, 59, 64})
        self.assertEqual(max_polyphony(out.instruments[0].notes), 6)

    def test_strong_short_note_and_quiet_passage_are_retained(self):
        midi = make([(40, 0, 0.05, 100), (43, 4, 4.2, 18), (45, 4.3, 4.5, 18)])
        out, _ = clean_notes(midi, "bass")
        self.assertEqual(len(out.instruments[0].notes), 3)

    def test_rejected_note_does_not_reappear_as_late_attack(self):
        midi = make([(40, 0, 0.2, 100), (41, 0, 0.8, 15)])
        out, _ = clean_notes(midi, "bass")
        self.assertEqual(
            [(n.pitch, n.start, n.end) for n in out.instruments[0].notes], [(40, 0, 0.2)]
        )

    def test_other_tracks_and_off_mode_unchanged(self):
        midi = make([(40, 0, 0.4, 90), (52, 0, 0.4, 65)])
        for stem in ("vocals", "drums", "piano", "other"):
            out, _ = clean_notes(midi, stem)
            self.assertEqual(
                [(n.pitch, n.start, n.end) for n in out.instruments[0].notes],
                [(40, 0, 0.4), (52, 0, 0.4)],
            )
        out, _ = clean_notes(midi, "bass", mode="off")
        self.assertEqual(len(out.instruments[0].notes), 2)

    def test_real_waveform_evidence_handles_silence_and_supported_note(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bass.wav"
            sr = 11025
            t = np.arange(sr) / sr
            sf.write(path, 0.1 * np.sin(2 * np.pi * librosa_frequency(40) * t), sr)
            midi = make([(40, 0.1, 0.9, 90), (52, 0.1, 0.9, 60)])
            out, _ = clean_notes(midi, "bass", wav=path)
            self.assertEqual([n.pitch for n in out.instruments[0].notes], [40])
            sf.write(path, np.zeros(sr), sr)
            silent, _ = clean_notes(midi, "bass", wav=path)
            self.assertEqual(silent.instruments[0].notes, [])


def librosa_frequency(pitch):
    return 440 * 2 ** ((pitch - 69) / 12)


class CleanupEngineTests(unittest.TestCase):
    def test_gentle_mode_preserves_more_polyphony(self):
        midi = make([(p, 0, 0.4, 90) for p in range(48, 56)])
        auto, _ = clean_notes(midi, "guitar")
        gentle, _ = clean_notes(midi, "guitar", mode="gentle")
        self.assertEqual(len(auto.instruments[0].notes), 6)
        self.assertEqual(len(gentle.instruments[0].notes), 8)

    def test_engine_automatically_writes_cleaned_notes_report_and_raw_backup(self):
        from unittest.mock import patch
        import json
        import audio2midi_core as core

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = root / "stems"
            sources.mkdir()
            output = root / "out"
            sr = 11025
            t = np.arange(sr) / sr
            for stem in ("bass", "guitar"):
                sf.write(sources / (stem + ".wav"), 0.1 * np.sin(2 * np.pi * 82.4 * t), sr)

            def predict(path, stem):
                if stem == "bass":
                    return make([(40, 0.1, 0.5, 90), (52, 0.1, 0.5, 50)])
                return make([(p, 0.1, 0.5, 90) for p in range(48, 56)])

            with (
                patch(
                    "sys.argv",
                    [
                        "audio2midi_core.py",
                        str(sources),
                        "-o",
                        str(output),
                        "--note-cleanup",
                        "auto",
                    ],
                ),
                patch.object(core, "prepare_wav", side_effect=lambda path, workdir: path),
                patch.object(core, "analyze_metronome", return_value=(120, np.array([]))),
                patch.object(core, "transcribe_stem", side_effect=predict),
            ):
                self.assertEqual(core.main(), 0)
            report = json.loads((output / "midi/relatorio_limpeza.json").read_text())
            self.assertEqual({r["stem"] for r in report["tracks"]}, {"bass", "guitar"})
            self.assertTrue((output / "antes_limpeza/bass.mid").is_file())
            combined = pretty_midi.PrettyMIDI(str(output / "midi/completo.mid"))
            self.assertLessEqual(
                max_polyphony(next(i for i in combined.instruments if i.name == "Bass").notes), 1
            )
            self.assertLessEqual(
                max_polyphony(next(i for i in combined.instruments if i.name == "Guitar").notes), 6
            )
