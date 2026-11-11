"""Testes unitários das melhorias 4.7:
1. Progressão harmônica cíclica: o piano e violão trocam de acorde compasso a compasso e cobrem a música toda.
2. Violão simples gerado (Acoustic Guitar - canal 3 / program 25) em colcheias afinado na cifra.
3. Rejeição de vazamento de bateria no piano de solo e acompanhamento.
4. Sincronização do bumbo ao baixo (batem juntos quando próximos) e sustentação contínua de groove do baixo.
5. Caixa e tons no tempo certo (viradas reais vs batidas de groove da grade).
"""

import sys
import unittest
import tempfile
from pathlib import Path
import pretty_midi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keyboard_enhancer import _get_chord_intervals, enhance_acoustic_guitar, enhance_keyboards
from bleed_cleaner import clean_midi_bleed
from solo_detector import split_piano_solo
from rhythm_lock import lock_rhythm
from drum_timing import quantize_drums
from note_cleanup import max_polyphony


class Melhorias47Tests(unittest.TestCase):
    def test_chord_intervals_cycle_through_progression(self):
        """Intervalos harmônicos avançam compasso a compasso e não travam em um único acorde."""
        chords = [(0.0, "D"), (3.5, "A"), (7.0, "Bm"), (10.5, "G")]
        intervals = _get_chord_intervals(chords, duration=60.0, bpm=135.0)
        # Deve cobrir a duração toda e ciclar pelos acordes
        self.assertGreater(len(intervals), 10)
        symbols = [iv["symbol"] for iv in intervals[:8]]
        self.assertEqual(symbols[:4], ["D", "A", "Bm", "G"])
        self.assertEqual(symbols[4:8], ["D", "A", "Bm", "G"])
        self.assertAlmostEqual(intervals[-1]["end"], 60.0, delta=2.0)

    def test_acoustic_guitar_generated_with_program_25_and_triads(self):
        """Violão simples é gerado em colcheias seguindo a tríade da cifra."""
        intervals = [
            {"start": 0.0, "end": 4.0, "symbol": "C", "triad": {0, 4, 7}, "root": 0, "bass": 0, "notes": {0, 4, 7}},
            {"start": 4.0, "end": 8.0, "symbol": "G", "triad": {7, 11, 2}, "root": 7, "bass": 7, "notes": {7, 11, 2}},
        ]
        guitar_inst = enhance_acoustic_guitar(None, intervals, bpm=120.0, duration=8.0)
        self.assertEqual(guitar_inst.program, 25)
        self.assertEqual(guitar_inst.name, "Acoustic Guitar")
        self.assertGreater(len(guitar_inst.notes), 10)
        # Todas as notas no primeiro intervalo pertencem à tríade de Dó
        c_notes = [n.pitch % 12 for n in guitar_inst.notes if n.start < 3.9]
        for pc in c_notes:
            self.assertIn(pc, {0, 4, 7})
        # Todas as notas no segundo intervalo pertencem à tríade de Sol
        g_notes = [n.pitch % 12 for n in guitar_inst.notes if n.start >= 4.0]
        for pc in g_notes:
            self.assertIn(pc, {7, 11, 2})

    def test_enhance_keyboards_creates_guitar_and_updates_combined(self):
        """enhance_keyboards salva guitar.mid e inclui violão no completo.mid."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir)
            # Cria completo.mid falso com baixo e bateria
            pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
            bass = pretty_midi.Instrument(program=33, name="Bass")
            bass.notes.append(pretty_midi.Note(80, 40, 0.0, 2.0))
            drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
            drums.notes.append(pretty_midi.Note(100, 36, 0.0, 0.1))
            pm.instruments.extend([bass, drums])
            pm.write(str(dir_path / "completo.mid"))

            res = enhance_keyboards(
                midi_dir=dir_path,
                bpm=120.0,
                chords=[(0.0, "D"), (2.0, "A")],
                duration=4.0,
            )
            self.assertTrue((dir_path / "guitar.mid").is_file())
            self.assertTrue((dir_path / "piano.mid").is_file())
            self.assertGreater(res["guitar_notes"], 0)

            # Verifica completo.mid atualizado
            updated_pm = pretty_midi.PrettyMIDI(str(dir_path / "completo.mid"))
            inst_names = [i.name for i in updated_pm.instruments]
            self.assertIn("Acoustic Guitar", inst_names)
            self.assertIn("Piano", inst_names)

    def test_bleed_cleaner_and_solo_reject_drum_transients(self):
        """Transientes rápidos de bateria são rejeitados de piano e solo."""
        piano_midi = pretty_midi.PrettyMIDI(initial_tempo=120.0)
        p_inst = pretty_midi.Instrument(program=0, name="Piano")
        # Nota normal de piano
        p_inst.notes.append(pretty_midi.Note(80, 60, 1.0, 2.0))
        # Transiente de bateria vazado no piano (muito curto, coincide com caixa em 0.5s)
        p_inst.notes.append(pretty_midi.Note(70, 72, 0.50, 0.56))
        piano_midi.instruments.append(p_inst)

        drums_midi = pretty_midi.PrettyMIDI(initial_tempo=120.0)
        d_inst = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
        d_inst.notes.append(pretty_midi.Note(100, 38, 0.50, 0.60))
        drums_midi.instruments.append(d_inst)

        stems_map = {"piano": piano_midi, "drums": drums_midi}
        removed = clean_midi_bleed(stems_map)
        self.assertGreater(removed.get("piano", 0), 0)
        self.assertEqual(len(p_inst.notes), 1)
        self.assertEqual(p_inst.notes[0].pitch, 60)

    def test_rhythm_lock_aligns_kicks_with_bass_and_sustains_groove(self):
        """Bumbo se alinha ao baixo quando próximos e baixo mantém legato sem paradas abruptas."""
        bass_inst = pretty_midi.Instrument(program=33, name="Bass")
        # Baixo toca em 0.02 e 1.01
        bass_inst.notes.append(pretty_midi.Note(90, 40, 0.02, 0.40))
        bass_inst.notes.append(pretty_midi.Note(90, 43, 1.01, 1.50))

        drum_inst = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
        # Bumbo toca em 0.00 e 1.00 (próximo ao baixo)
        drum_inst.notes.append(pretty_midi.Note(100, 36, 0.00, 0.10))
        drum_inst.notes.append(pretty_midi.Note(100, 36, 1.00, 1.10))
        # Bumbo adicional em 2.00 (sem nota de baixo)
        drum_inst.notes.append(pretty_midi.Note(100, 36, 2.00, 2.10))

        lock_rhythm([bass_inst, drum_inst], bpm=120.0)

        # Bumbos devem bater exatamente no tempo alinhado
        self.assertEqual(len(drum_inst.notes), 3)
        self.assertEqual(drum_inst.notes[0].start, bass_inst.notes[0].start)
        self.assertEqual(drum_inst.notes[1].start, bass_inst.notes[1].start)
        # Baixo não corta prematuramente (groove legato sustentado)
        self.assertGreater(bass_inst.notes[0].end, 0.8)
        self.assertEqual(max_polyphony(bass_inst.notes), 1)

    def test_drum_timing_groove_quantization(self):
        """Batidas normais de caixa na cabeça do tempo não disparam subdivisão de virada."""
        midi = pretty_midi.PrettyMIDI(initial_tempo=120.0)
        drum = pretty_midi.Instrument(0, is_drum=True, name="Drums")
        # Caixa no tempo 2 (1.0s) e tempo 4 (2.0s)
        drum.notes.append(pretty_midi.Note(95, 38, 1.02, 1.12))
        drum.notes.append(pretty_midi.Note(95, 38, 2.01, 2.11))
        midi.instruments.append(drum)

        grid_times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
        quantize_drums(midi, bpm=120.0, grid_times=grid_times, grid=16)

        starts = [n.start for n in drum.notes]
        self.assertEqual(starts, [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
