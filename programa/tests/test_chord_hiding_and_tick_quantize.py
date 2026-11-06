import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mido
import pretty_midi

from lyrics import build_lyric_events, export_karaoke
from midi_timing import quantize_midi_file_ticks
from music_guide import is_chord_symbol, parse_guide


class ChordHidingAndTickQuantizeTests(unittest.TestCase):
    def test_chord_recognition_and_clean_lyrics(self):
        """Garante que acordes como Bm, A, Em, D sejam identificados e ocultados da letra cantada."""
        lyrics_with_chords = (
            "Encontrei uns fio de cabelo\n"
            "Seu por todo meu apê\n"
            "A\n"
            "Na hora as nostalgias\n"
            "Começaram a bater\n"
            "Bm\n"
            "Sua mão no box do banheiro\n"
            "Em\n"
            "Tão aqui perguntando de tu\n"
            "D\n"
            "Altos flashback, mó saudade, né?"
        )
        plain, marks = parse_guide(lyrics_with_chords)

        # 1. Os acordes devem ser extraídos como marcas harmônicas
        symbols = [m["symbol"] for m in marks]
        self.assertIn("A", symbols)
        self.assertIn("Bm", symbols)
        self.assertIn("Em", symbols)
        self.assertIn("D", symbols)

        # 2. A letra limpa não pode ter Bm, Em ou D como versos
        lines = [line.strip() for line in plain.splitlines() if line.strip()]
        self.assertNotIn("Bm", lines)
        self.assertNotIn("Em", lines)
        self.assertNotIn("D", lines)
        self.assertIn("Encontrei uns fio de cabelo", lines)
        self.assertIn("Começaram a bater", lines)
        self.assertIn("Sua mão no box do banheiro", lines)

    def test_karaoke_events_filter_out_all_chords(self):
        """Garante que nenhum token de acorde seja escrito na faixa de karaokê."""
        words = [
            {"text": "Na", "start": 10.0, "end": 10.5},
            {"text": "hora", "start": 10.5, "end": 11.0},
            {"text": "Bm", "start": 12.0, "end": 12.5},  # Acorde intruso
            {"text": "sua", "start": 13.0, "end": 13.5},
            {"text": "mão", "start": 13.5, "end": 14.0},
        ]
        clean_words = [w for w in words if not is_chord_symbol(w["text"])]
        self.assertEqual(len(clean_words), 4)

        lyric_events, plain, _ = build_lyric_events(
            detected_words=clean_words,
            manual_text="Na hora\nsua mão",
            duration=20.0,
        )

        texts = [txt.strip() for _, txt in lyric_events if txt.strip()]
        self.assertNotIn("Bm", texts)
        self.assertIn("Na", texts)
        self.assertTrue(any("ho" in t for t in texts))

    def test_quantize_midi_file_ticks(self):
        """Garante que quantize_midi_file_ticks elimine resíduos de ponto flutuante e trave na grade."""
        with tempfile.TemporaryDirectory() as tmpdir:
            midi_path = Path(tmpdir) / "test.mid"
            pm = pretty_midi.PrettyMIDI(initial_tempo=133.0, resolution=960)
            inst = pretty_midi.Instrument(program=0, name="Piano")
            # Adiciona notas com resíduo de fase (ex: 218 ticks em vez de 240)
            inst.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=0.1023, end=0.450))
            inst.notes.append(pretty_midi.Note(velocity=80, pitch=64, start=0.4611, end=0.900))
            pm.instruments.append(inst)
            pm.write(str(midi_path))

            # Quantiza os ticks diretamente no MidiFile
            quantize_midi_file_ticks(midi_path, step=240)

            # Verifica se todas as notas NoteOn têm tick múltiplo exato de 240
            mf = mido.MidiFile(str(midi_path))
            for tr in mf.tracks:
                tick = 0
                for msg in tr:
                    tick += msg.time
                    if msg.type == "note_on" and msg.velocity > 0:
                        self.assertEqual(tick % 240, 0)


if __name__ == "__main__":
    unittest.main()
