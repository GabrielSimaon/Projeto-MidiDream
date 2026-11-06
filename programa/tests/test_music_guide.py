import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mido
from music_guide import parse_guide, chord_notes, add_chord_track


class GuideTests(unittest.TestCase):
    def test_parse(self):
        text, marks = parse_guide("[C]Eu sigo [00:12.5 Am]o caminho\n[G7]volto")
        self.assertEqual(text, "Eu sigo o caminho\nvolto")
        self.assertEqual(marks[1]["seconds"], 12.5)
        self.assertEqual(chord_notes("Am"), [57, 60, 64])
        self.assertIn(38, chord_notes("C/D"))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_guide("[H]erro")

    def test_track_timing_and_note_off(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "song.mid"
            midi = mido.MidiFile(type=1, ticks_per_beat=960)
            midi.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=600000)]))
            midi.save(path)
            text, marks = parse_guide("[00:01.2 C]Eu [00:02.4 G]vou")
            add_chord_track(path, text, marks, [], 3.6)
            out = mido.MidiFile(path)
            notes = [m for m in out.tracks[-1] if m.type in ("note_on", "note_off")]
            self.assertEqual(len(notes), 12)
            self.assertEqual(notes[0].time, 0)  # marker carries initial delay
            self.assertEqual(sum(m.time for m in out.tracks[-1]), 5760)
            self.assertTrue(all(m.channel == 5 for m in notes))
