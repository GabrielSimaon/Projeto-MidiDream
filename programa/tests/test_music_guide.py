import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mido
from music_guide import parse_guide, chord_notes, add_chord_track, chord_triad_pcs
from keyboard_enhancer import _chord_voicing


class GuideTests(unittest.TestCase):
    def test_chord_triad_fundamental_coverage(self):
        """Garante que a tríade fundamental de acordes com extensões é identificada perfeitamente."""
        root_c, triad_c = chord_triad_pcs("C")
        self.assertEqual(root_c, 0)
        self.assertEqual(triad_c, {0, 4, 7})

        root_f9, triad_f9 = chord_triad_pcs("F9")
        self.assertEqual(root_f9, 5)
        self.assertEqual(triad_f9, {5, 9, 0})  # F, A, C

        root_am7, triad_am7 = chord_triad_pcs("Am7")
        self.assertEqual(root_am7, 9)
        self.assertEqual(triad_am7, {9, 0, 4})  # A, C, E

        root_em7, triad_em7 = chord_triad_pcs("Em7")
        self.assertEqual(root_em7, 4)
        self.assertEqual(triad_em7, {4, 7, 11})  # E, G, B

        root_g, triad_g = chord_triad_pcs("G")
        self.assertEqual(root_g, 7)
        self.assertEqual(triad_g, {7, 11, 2})  # G, B, D

    def test_chord_voicing_contains_all_triad_tones(self):
        """Garante que o voicing gerado para o piano contém 100% das 3 notas da tríade sem descartar nenhuma."""
        for sym in ["C", "F9", "Am7", "G", "Em7", "Dm"]:
            _, triad = chord_triad_pcs(sym)
            voicing = _chord_voicing(triad, low_midi=60, high_midi=76)
            voicing_pcs = {p % 12 for p in voicing}
            self.assertEqual(voicing_pcs, triad, f"Voicing {voicing} for {sym} missing triad tone from {triad}")
            self.assertEqual(len(voicing), 3)

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
