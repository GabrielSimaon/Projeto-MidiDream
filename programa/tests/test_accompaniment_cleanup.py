"""Fantasmas, contexto harmônico e preservação do arquivo MIDI."""

import sys, tempfile, unittest
from pathlib import Path
import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accompaniment_cleanup import filter_midi, read_notes, timed_chords, key_scale
from midi_timing import TempoMap


def fixture(path, items, name="Piano", channel=2, program=0):
    m = mido.MidiFile(type=1, ticks_per_beat=960)
    m.tracks.append(
        mido.MidiTrack(
            [
                mido.MetaMessage("set_tempo", tempo=500000),
                mido.MetaMessage("set_tempo", tempo=750000, time=3840),
            ]
        )
    )
    events = [
        (0, 0, mido.MetaMessage("track_name", name=name)),
        (0, 0, mido.Message("program_change", channel=channel, program=program)),
        (250, 0, mido.Message("control_change", channel=channel, control=7, value=93)),
    ]
    for p, start, end, v in items:
        events.extend(
            [
                (start, 2, mido.Message("note_on", channel=channel, note=p, velocity=v)),
                (end, 1, mido.Message("note_off", channel=channel, note=p, velocity=0)),
            ]
        )
    track = mido.MidiTrack()
    previous = 0
    for tick, _, msg in sorted(events, key=lambda item: (item[0], item[1])):
        track.append(msg.copy(time=tick - previous))
        previous = tick
    m.tracks.append(track)
    m.tracks.append(
        mido.MidiTrack(
            [
                mido.MetaMessage("track_name", name="Lyrics"),
                mido.MetaMessage("lyrics", text="Meu ", time=2400),
                mido.MetaMessage("lyrics", text="refrão", time=2000),
            ]
        )
    )
    m.save(path)


def metadata(path):
    result = []
    for track in mido.MidiFile(path, charset="latin1").tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type not in ("note_on", "note_off", "end_of_track"):
                result.append((tick, msg.copy(time=0)))
    return result


class AccompanimentCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "in.mid"
        self.output = self.root / "out.mid"

    def tearDown(self):
        self.temp.cleanup()

    def notes(self):
        return [n for _, _, notes in read_notes(mido.MidiFile(self.output)) for n in notes]

    def test_removes_relative_weak_fragment_and_keeps_strong_short_note(self):
        fixture(
            self.source,
            [
                (60, 0, 500, 90),
                (64, 500, 1000, 90),
                (67, 1000, 1500, 90),
                (61, 520, 600, 15),
                (72, 900, 980, 100),
            ],
        )
        before = metadata(self.source)
        report = filter_midi(self.source, self.output)
        self.assertEqual(report["removed"], 1)
        self.assertEqual(metadata(self.output), before)
        self.assertTrue(any(n.pitch == 72 for n in self.notes()))

    def test_quiet_passage_not_removed_only_for_low_volume(self):
        fixture(
            self.source,
            [
                (p, index * 250, index * 250 + 100, 18)
                for index, p in enumerate((60, 62, 64, 65, 67, 69))
            ],
        )
        report = filter_midi(self.source, self.output)
        self.assertEqual(report["removed"], 0)

    def test_weak_note_corrected_by_local_chord_without_forcing_all_notes(self):
        fixture(
            self.source,
            [
                (64, 1000, 2800, 90),
                (67, 1000, 2800, 90),
                (61, 1920, 2300, 15),
                (66, 2200, 2400, 95),
            ],
        )
        report = filter_midi(self.source, self.output, chords=[(0.0, "C")], key="C maior")
        self.assertEqual(report["corrected"], 1)
        self.assertTrue(any(n.pitch == 60 and n.velocity == 15 for n in self.notes()))
        self.assertTrue(any(n.pitch == 66 and n.velocity == 95 for n in self.notes()))

    def test_valid_passing_note_is_not_forced_to_chord(self):
        fixture(self.source, [(64, 1000, 2800, 90), (67, 1000, 2800, 90), (62, 1920, 2300, 15)])
        report = filter_midi(self.source, self.output, chords=[(0.0, "C")], key="C maior")
        self.assertEqual(report["corrected"], 0)
        self.assertEqual(report["removed"], 0)

    def test_chord_borrowed_from_other_key_is_preserved(self):
        fixture(self.source, [(64, 1000, 2800, 90), (67, 1000, 2800, 90), (66, 1920, 2300, 15)])
        report = filter_midi(self.source, self.output, chords=[(0.0, "D")], key="C maior")
        self.assertEqual(report["corrected"], 0)
        self.assertTrue(any(n.pitch == 66 for n in self.notes()))

    def test_tie_without_harmonic_evidence_is_not_randomly_rounded_down(self):
        fixture(self.source, [(64, 1000, 2800, 90), (67, 1000, 2800, 90), (61, 1920, 2300, 30)])
        report = filter_midi(self.source, self.output, key="C maior")
        self.assertEqual(report["corrected"], 0)
        self.assertTrue(any(n.pitch == 61 for n in self.notes()))

    def test_vocals_bass_and_drums_are_protected(self):
        items = [(60, 0, 500, 100), (61, 250, 300, 10)]
        for name, channel, program in [("Vocal Flute", 1, 73), ("Bass", 0, 33), ("Drums", 9, 0)]:
            fixture(self.source, items, name, channel, program)
            report = filter_midi(self.source, self.output, key="C maior")
            self.assertEqual(report["removed"], 0)
            self.assertEqual(report["corrected"], 0)

    def test_corrected_duplicate_is_removed_instead_of_retriggering_main_note(self):
        fixture(
            self.source,
            [
                (60, 1000, 2800, 90),
                (64, 1000, 2800, 90),
                (67, 1000, 2800, 90),
                (61, 1920, 2300, 15),
            ],
        )
        report = filter_midi(self.source, self.output, chords=[(0.0, "C")], key="C maior")
        self.assertEqual(report["removed"], 1)
        self.assertEqual(sum(n.pitch == 60 for n in self.notes()), 1)

    def test_timed_chords_require_alignment_or_explicit_time(self):
        marks = [
            {"symbol": "C", "offset": 0, "seconds": None},
            {"symbol": "G", "offset": 4, "seconds": 3.0},
        ]
        self.assertEqual(timed_chords("Meu refrão", marks, [], 5), [(3.0, "G")])
        aligned = [
            {"text": "Meu", "start": 1.0, "end": 1.5},
            {"text": "refrão", "start": 2.0, "end": 2.5},
        ]
        self.assertEqual(timed_chords("Meu refrão", marks, aligned, 5), [(1.0, "C"), (3.0, "G")])

    def test_vocal_c_is_not_a_global_key_estimate(self):
        from accompaniment_cleanup import Note

        fixture(self.source, [(66, 1000, 2800, 90), (69, 1000, 2800, 90), (61, 1920, 2300, 30)])
        vocal = Note(60, 90, 0, 4, 1, 0, 1, 73)
        report = filter_midi(self.source, self.output, vocal_notes=[vocal])
        self.assertEqual(report["scale"], [])
        self.assertEqual(report["corrected"], 0)

    def test_clock_roundtrip_at_tempo_changes(self):
        fixture(self.source, [(60, 0, 5500, 90)])
        clock = TempoMap(mido.MidiFile(self.source))
        for tick in (0, 960, 3840, 4500, 5500):
            self.assertEqual(clock.seconds_to_ticks(clock.ticks_to_seconds(tick)), tick)

    def test_pipeline_filters_combined_and_individual_with_same_chord_context(self):
        import json, shutil, numpy as np, soundfile as sf
        from unittest.mock import patch
        from pipeline import run_pipeline

        audio = self.root / "piano.wav"
        sf.write(audio, np.zeros(16000 * 4), 16000)

        def engine(command, **kwargs):
            target = Path(command[command.index("-o") + 1]) / "midi"
            target.mkdir(parents=True)
            fixture(
                target / "completo.mid",
                [(64, 1000, 2800, 90), (67, 1000, 2800, 90), (61, 1920, 2300, 15)],
            )
            shutil.copy2(target / "completo.mid", target / "other.mid")
            return "ok"

        with patch("pipeline._run", side_effect=engine):
            result = run_pipeline(
                self.root / "job",
                audio_path=audio,
                separate=False,
                recognize_lyrics=False,
                manual_lyrics="[00:00 C]Minha música",
                bpm_override=120,
                require_guide=True,
            )
        report = json.loads(
            (Path(result["result_dir"]) / "relatorio_filtro_acompanhamento.json").read_text()
        )
        self.assertEqual(report["files"]["completo.mid"]["corrected"], 1)
        self.assertEqual(report["files"]["other.mid"]["corrected"], 1)
        self.assertTrue((Path(result["result_dir"]) / "midi_antes_filtro/other.mid").exists())

    def test_style_does_not_blindly_snap_before_context_filter(self):
        import pretty_midi
        from musical_style import apply_style

        midi = pretty_midi.PrettyMIDI()
        instrument = pretty_midi.Instrument(0)
        instrument.notes = [pretty_midi.Note(15, 61, 0.1, 0.3)]
        midi.instruments = [instrument]
        revised = apply_style(midi, "piano", 120, [], 0, {"scale": [0, 2, 4, 5, 7, 9, 11]})
        self.assertEqual(revised.instruments[0].notes[0].pitch, 61)

    def test_minor_key_and_filter_off(self):
        self.assertEqual(key_scale("A menor"), {0, 2, 4, 5, 7, 9, 11})
        fixture(self.source, [(60, 0, 500, 90), (61, 100, 150, 10)])
        report = filter_midi(self.source, self.output, level="off")
        self.assertEqual(report["removed"], 0)
        self.assertEqual(len(self.notes()), 2)


if __name__ == "__main__":
    unittest.main()


class ChordLockTests(unittest.TestCase):
    """Nível "chords": o acompanhamento passa a obedecer às cifras do usuário."""

    def run_lock(self, items, chords):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "in.mid"
            fixture(source, items)
            report = filter_midi(source, Path(folder) / "out.mid", level="chords", chords=chords)
            tracks = read_notes(mido.MidiFile(Path(folder) / "out.mid"))
            notes = next(n for name, _, n in tracks if name == "Piano")
            return report, notes

    def test_off_chord_notes_snap_to_the_chord(self):
        # Dó maior durante 8 s; Dó# e Fá# (desafinados) são fortes o bastante para sobreviver ao filtro.
        items = [(60, 0, 960, 90), (64, 0, 960, 90), (67, 0, 960, 90), (61, 1000, 1900, 90), (66, 2000, 2900, 90)]
        report, notes = self.run_lock(items, [(0.0, "C")])
        self.assertTrue(all(n.pitch % 12 in (0, 4, 7) for n in notes))
        self.assertGreaterEqual(report["chord_lock"]["snapped"], 2)

    def test_note_is_cut_at_chord_change_and_low_notes_move_up(self):
        items = [(64, 0, 4000, 90), (40, 0, 900, 90)]  # Mi dura 4 s; 40 está abaixo de Dó3
        _, notes = self.run_lock(items, [(0.0, "C"), (0.5, "F")])
        mi = next(n for n in notes if n.pitch == 64)
        self.assertLessEqual(mi.end, 0.5 + 0.01)
        self.assertTrue(all(n.pitch >= 48 for n in notes))

    def test_voice_limit_and_no_chords_means_untouched(self):
        items = [(p, 0, 960, 90 - i) for i, p in enumerate((60, 64, 67, 72, 76, 79, 84, 88))]
        _, notes = self.run_lock(items, [(0.0, "C")])
        self.assertLessEqual(len(notes), 5)
        report, untouched = self.run_lock(items, [])
        self.assertNotIn("chord_lock", report)
        self.assertEqual(len(untouched), 8)
