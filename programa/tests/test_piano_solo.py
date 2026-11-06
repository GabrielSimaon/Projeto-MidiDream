"""Testes unitários para o módulo de solo e a faixa dedicada PianoSolo."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
import tempfile
import pretty_midi
import mido

from solo_detector import detect_solo_intervals, split_piano_solo, is_in_solo_window
from instrument_catalog import extra_kind, EXTRA, classify_name
from lyrics import remap_channels
from auto_mix import calculate_relative_volumes
from midi_mix import validate_mix, mix_midi


class TestPianoSolo(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_solo_intervals_comeco_meio_final(self):
        """Verifica a identificação precisa de solos no começo, meio e final da música."""
        words = [
            {"start": 5.0, "end": 5.5, "text": "Primeira"},
            {"start": 5.6, "end": 6.2, "text": "palavra"},
            {"start": 6.3, "end": 7.0, "text": "cantada"},
            # Gap de 4.0 segundos (Solo do meio)
            {"start": 11.0, "end": 11.5, "text": "Refrão"},
            {"start": 11.6, "end": 12.0, "text": "começa"},
            {"start": 12.1, "end": 13.0, "text": "aqui"},
            # Gap de 5.0 segundos (Outro solo do meio / interlúdio)
            {"start": 18.0, "end": 18.5, "text": "Segunda"},
            {"start": 18.6, "end": 19.0, "text": "estrofe"},
            {"start": 19.1, "end": 20.0, "text": "finaliza"},
        ]
        duration = 30.0  # Música vai até 30s (última palavra em 20s -> solo final de 10s)

        intervals = detect_solo_intervals(duration=duration, detected_words=words, min_middle_gap=2.5)

        types = [iv["type"] for iv in intervals]
        self.assertIn("comeco", types)
        self.assertIn("meio", types)
        self.assertIn("final", types)

        # Começo deve cobrir de 0 até o início do vocal (~5.0s)
        comeco_iv = next(iv for iv in intervals if iv["type"] == "comeco")
        self.assertEqual(comeco_iv["start"], 0.0)
        self.assertAlmostEqual(comeco_iv["end"], 4.85, delta=0.2)

        # Meio deve capturar os gaps de 7.0-11.0s e 13.0-18.0s
        meio_ivs = [iv for iv in intervals if iv["type"] == "meio"]
        self.assertEqual(len(meio_ivs), 2)
        self.assertTrue(any(6.9 <= iv["start"] and iv["end"] <= 11.1 for iv in meio_ivs))
        self.assertTrue(any(12.9 <= iv["start"] and iv["end"] <= 18.1 for iv in meio_ivs))

        # Final deve cobrir do fim da última palavra (20.0s) até a duração (30.0s)
        final_iv = next(iv for iv in intervals if iv["type"] == "final")
        self.assertAlmostEqual(final_iv["start"], 20.1, delta=0.2)
        self.assertEqual(final_iv["end"], 30.0)

    def test_split_piano_solo_exclusive_function(self):
        """Verifica que o PianoSolo toca única e exclusivamente solo e não se mistura com a base."""
        # Cria um arquivo piano.mid sintetizado
        pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
        inst = pretty_midi.Instrument(program=0, name="Piano")

        # 1. Notas da introdução (0s a 4s)
        # Solo melódico: Dó5 (72) -> Ré5 (74)
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=72, start=1.0, end=1.5))
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=74, start=2.0, end=2.5))
        # Base harmônica de intro: Dó3 (48), Sol3 (55)
        inst.notes.append(pretty_midi.Note(velocity=70, pitch=48, start=1.0, end=1.5))
        inst.notes.append(pretty_midi.Note(velocity=70, pitch=55, start=1.0, end=1.5))

        # 2. Notas durante a estrofe cantada (5s a 10s):
        # Apenas acompanhamento da base! (PianoSolo não deve tocar aqui)
        inst.notes.append(pretty_midi.Note(velocity=75, pitch=60, start=6.0, end=6.5))
        inst.notes.append(pretty_midi.Note(velocity=75, pitch=64, start=6.0, end=6.5))
        inst.notes.append(pretty_midi.Note(velocity=75, pitch=67, start=6.0, end=6.5))

        # 3. Notas do solo do meio (11s a 15s):
        # Linha virtuosa de solo: Mi5 (76) -> Sol5 (79)
        inst.notes.append(pretty_midi.Note(velocity=85, pitch=76, start=12.0, end=12.5))
        inst.notes.append(pretty_midi.Note(velocity=85, pitch=79, start=13.0, end=13.5))

        # 4. Notas do solo final (21s a 25s):
        # Frase de encerramento: Lá5 (81)
        inst.notes.append(pretty_midi.Note(velocity=90, pitch=81, start=22.0, end=23.0))

        pm.instruments.append(inst)
        piano_path = self.dir_path / "piano.mid"
        solo_path = self.dir_path / "piano_solo.mid"
        pm.write(str(piano_path))

        words = [
            {"start": 5.0, "end": 10.0, "text": "Estrofe cantada"},
            {"start": 16.0, "end": 20.0, "text": "Refrão cantado"},
        ]
        duration = 26.0

        intervals = detect_solo_intervals(duration=duration, detected_words=words, min_middle_gap=2.5)
        report = split_piano_solo(
            piano_midi_path=piano_path,
            solo_intervals=intervals,
            output_solo_path=solo_path,
            output_piano_path=piano_path,
            bpm=120.0,
        )

        self.assertEqual(report["status"], "success")
        self.assertGreater(report["solo_notes"], 0)
        self.assertGreater(report["base_notes"], 0)

        # Lê os arquivos gerados
        pm_solo = pretty_midi.PrettyMIDI(str(solo_path))
        pm_base = pretty_midi.PrettyMIDI(str(piano_path))

        solo_pitches = [n.pitch for n in pm_solo.instruments[0].notes]
        base_pitches = [n.pitch for n in pm_base.instruments[0].notes]

        # Piano Solo deve conter as notas agudas da intro, meio e fim (72, 74, 76, 79, 81)
        self.assertIn(72, solo_pitches)
        self.assertIn(74, solo_pitches)
        self.assertIn(76, solo_pitches)
        self.assertIn(79, solo_pitches)
        self.assertIn(81, solo_pitches)

        # Piano Solo NÃO deve conter as notas da estrofe cantada (6s)
        solo_in_verse = [n for n in pm_solo.instruments[0].notes if 5.0 <= n.start <= 10.0]
        self.assertEqual(len(solo_in_verse), 0, "PianoSolo não pode tocar durante a parte cantada!")

        # Piano Base deve manter o acompanhamento da estrofe cantada
        base_in_verse = [n for n in pm_base.instruments[0].notes if 5.0 <= n.start <= 10.0]
        self.assertEqual(len(base_in_verse), 3, "Piano base deve manter os acordes de acompanhamento!")

        # O solo não deve mais estar na base
        self.assertNotIn(81, base_pitches, "Solo final não pode estar misturado na base!")

    def test_instrument_catalog_and_channel_mapping(self):
        """Verifica o reconhecimento de Piano Solo e mapeamento no Canal 8."""
        self.assertEqual(extra_kind("Piano Solo"), "piano_solo")
        self.assertEqual(extra_kind("pianosolo"), "piano_solo")
        self.assertEqual(extra_kind("Solo Piano"), "piano_solo")
        self.assertEqual(classify_name("piano_solo.mid"), "piano_solo")

        self.assertEqual(EXTRA["piano_solo"][2], 8)

        # Testa remap_channels
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=0, name="Piano Solo")
        inst.notes.append(pretty_midi.Note(velocity=100, pitch=72, start=0.0, end=1.0))
        pm.instruments.append(inst)
        test_file = self.dir_path / "piano_solo_test.mid"
        pm.write(str(test_file))

        remap_channels(test_file)

        mf = mido.MidiFile(str(test_file), charset="latin1")
        channels = [m.channel for t in mf.tracks for m in t if hasattr(m, "channel")]
        self.assertIn(8, channels, "Piano Solo deve estar mapeado no Canal 8 (Canal 9 GM)!")

    def test_midi_mix_and_auto_mix(self):
        """Verifica que o mixer aceita e aplica volume independente para o Piano Solo."""
        settings = validate_mix({"piano_solo": 125, "piano": 90})
        self.assertEqual(settings["piano_solo"], 125)
        self.assertEqual(settings["piano"], 90)

        vols = calculate_relative_volumes(self.dir_path)
        self.assertIn("piano_solo", vols)
        self.assertGreaterEqual(vols["piano_solo"], 105)


if __name__ == "__main__":
    unittest.main()
