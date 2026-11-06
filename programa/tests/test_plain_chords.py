"""Cifras no formato copiado de sites, sem confundir palavras da letra."""

import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from music_guide import chord_notes, parse_guide, validate_required_inputs, add_chord_track
import mido


class PlainChordTests(unittest.TestCase):
    def test_requested_chords_have_different_harmony(self):
        self.assertEqual(chord_notes("G4"), [55, 60, 62])
        self.assertEqual(chord_notes("G7"), [55, 59, 62, 65])
        self.assertEqual(chord_notes("G7M"), [55, 59, 62, 66])
        self.assertEqual(chord_notes("Gmaj7"), chord_notes("GM7"))
        self.assertEqual(chord_notes("G7+"), chord_notes("G7M"))
        self.assertEqual(chord_notes("Cmaj9"), [48, 52, 55, 59, 62])

    def test_common_extensions_inversions_and_alterations(self):
        cases = {
            "Em7": [52, 55, 59, 62],
            "C9": [48, 52, 55, 58, 62],
            "Cadd9": [48, 52, 55, 62],
            "F#m7(b5)": [54, 57, 60, 64],
            "F#7M(9)": [54, 58, 61, 65, 68],
            "C/G": [43, 48, 52, 55],
            "G6/9": [55, 59, 62, 64, 69],
            "G7(4)": [55, 60, 62, 65],
            "Gdim7": [55, 58, 61, 64],
            "G5": [55, 62],
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(chord_notes(name), expected)
        self.assertEqual(chord_notes("G°7"), chord_notes("Gdim7"))
        self.assertEqual(chord_notes("F♯m7"), chord_notes("F#m7"))

    def test_chord_line_attaches_by_column_to_lyrics(self):
        text, marks = parse_guide("G       D\nHoje eu volto\nEm7     C9\nEu sigo aqui")
        self.assertEqual(text, "Hoje eu volto\nEu sigo aqui")
        self.assertEqual([m["symbol"] for m in marks], ["G", "D", "Em7", "C9"])
        self.assertEqual(marks[0]["offset"], 0)
        self.assertEqual(marks[1]["offset"], text.index("volto"))
        self.assertEqual(marks[2]["offset"], text.index("Eu sigo"))

    def test_inline_bare_chords_and_brackets_both_work(self):
        text, marks = parse_guide("G Hoje eu volto G7M no passado")
        self.assertEqual(text.strip(), "Hoje eu volto  no passado")
        self.assertEqual([m["symbol"] for m in marks], ["G", "G7M"])
        self.assertEqual(parse_guide("[G4]Hoje [G7M]volto")[0], "Hoje volto")

    def test_articles_and_prepositions_do_not_become_chords_in_lyrics(self):
        text, marks = parse_guide("G\nA vida E o amor Em casa\nAmor e saudade")
        self.assertEqual(text, "A vida E o amor Em casa\nAmor e saudade")
        self.assertEqual([m["symbol"] for m in marks], ["G"])

    def test_site_headers_spaces_and_html(self):
        guide = "[ B9]\\\n&#x20;Eu admiro você\\\n[Segunda Parte]\\\n F#4      F#7M(9)\\\nTem que ter peito"
        text, marks = parse_guide(guide)
        self.assertNotIn("Segunda Parte", text)
        self.assertNotIn("&#", text)
        self.assertNotIn("\\", text)
        self.assertEqual([m["symbol"] for m in marks], ["B9", "F#4", "F#7M(9)"])

    def test_inline_section_label_is_not_a_chord(self):
        text, marks = parse_guide("[Intro] G D\nHoje eu volto")
        self.assertEqual(text, "Hoje eu volto")
        self.assertEqual([m["symbol"] for m in marks], ["G", "D"])

    def test_invalid_extensions_are_not_silently_guessed(self):
        for symbol in ("G8", "G7H", "G7M(9", "H", "Gmaug", "G/ZZ"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                chord_notes(symbol)
        with self.assertRaises(ValueError):
            parse_guide("G4 G7H\nMinha música")

    def test_plain_chords_satisfy_required_form(self):
        self.assertEqual(validate_required_inputs("G4 G7M\nMinha música", 158), 158.0)
        with self.assertRaises(ValueError):
            validate_required_inputs("G4 G7M", 158)

    def test_pipeline_uses_clean_lyrics_and_expanded_chords(self):
        import numpy as np, soundfile as sf
        from pipeline import run_pipeline
        from lyrics import read_timed_lyrics
        from test_studio import fixture

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "vocals.wav"
            sf.write(audio, np.zeros(16000 * 4), 16000)

            def engine(command, **kwargs):
                target = Path(command[command.index("-o") + 1]) / "midi"
                target.mkdir(parents=True)
                fixture(target / "completo.mid")
                return "ok"

            with patch("pipeline._run", side_effect=engine):
                result = run_pipeline(
                    root / "job",
                    audio_path=audio,
                    separate=False,
                    recognize_lyrics=False,
                    bpm_override=120,
                    require_guide=True,
                    chord_accompaniment=True,
                    manual_lyrics="G4        G7M\nHoje volto de novo",
                )
            words = "".join(x["text"] for x in read_timed_lyrics(result["karaoke_kar"]))
            self.assertIn("Hoje", words)
            self.assertNotIn("G4", words)
            self.assertNotIn("G7M", words)
            midi = mido.MidiFile(result["karaoke_kar"], charset="latin1")
            marks = [m.text for t in midi.tracks for m in t if m.type == "marker"]
            self.assertEqual(marks, ["G4", "G7M"])


if __name__ == "__main__":
    unittest.main()
