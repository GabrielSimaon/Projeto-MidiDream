import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from instrument_detection import decide_presence
from instrument_catalog import EXTRA


class InstrumentTests(unittest.TestCase):
    def test_absent_instruments_never_forced_to_pick_a_winner(self):
        result = decide_presence([{"Piano": 0.8, "Accordion": 0.01, "Electric guitar": 0.02}, {"Piano": 0.7}])
        self.assertTrue(result["piano"]["present"])
        self.assertFalse(result["accordion"]["present"])
        self.assertFalse(result["electric_guitar"]["present"])
        self.assertFalse(result["acoustic_guitar"]["present"])
        self.assertFalse(any(row["present"] for row in decide_presence([]).values()))

    def test_transient_uncertainty_and_multiple_simultaneous_instruments(self):
        result = decide_presence([{"Accordion": 0.5, "Acoustic guitar": 0.4}, {"Accordion": 0.45, "Acoustic guitar": 0.4}])
        self.assertTrue(result["accordion"]["present"])
        self.assertTrue(result["acoustic_guitar"]["present"])
        self.assertFalse(decide_presence([{"Accordion": 0.5}, {}])["accordion"]["present"])
        self.assertTrue(decide_presence([{"Accordion": 0.9}, {}])["accordion"]["present"])

    def test_names_programs_and_channels_preserved_through_mix(self):
        import mido
        from pipeline import classify_stem_name
        from audio2midi_core import classify_stem, GM_PROGRAMS
        from lyrics import remap_channels
        from midi_mix import mix_midi
        from accompaniment_cleanup import _target
        with tempfile.TemporaryDirectory() as folder:
            midi = mido.MidiFile()
            for kind, (program, name, channel) in EXTRA.items():
                self.assertEqual(classify_stem_name(kind + ".wav"), kind)
                self.assertEqual(classify_stem(Path(kind + ".mp3")), kind)
                self.assertEqual(GM_PROGRAMS[kind], (program, name))
                midi.tracks.append(mido.MidiTrack([mido.MetaMessage("track_name", name=name),
                    mido.Message("program_change", program=program),
                    mido.Message("note_on", note=60, velocity=90), mido.Message("note_off", note=60, time=480)]))
            path = Path(folder) / "all.mid"
            midi.save(path)
            remap_channels(path)
            result = mix_midi(mido.MidiFile(path), {"piano_program": 16})
            self.assertEqual([(next(m.channel for m in t if m.type == "program_change"),next(m.program for m in t if m.type == "program_change")) for t in result.tracks], [(5,21),(6,25),(7,27)])
            self.assertFalse(_target("Accordion", 21, 5))

    def test_overlap_processing_keeps_length_and_silence(self):
        import torch
        import numpy as np
        from instrument_worker import extract
        class Encoder:
            def get_query_embed(self, **kwargs):
                return torch.ones(1, 512)
        class Model:
            query_encoder = Encoder()
            def ss_model(self, data):
                return {"waveform": data["mixture"] * 0.5}
        audio = np.zeros(32000 * 13 + 37, dtype=np.float32)
        audio[100000:300000] = 0.25
        result = extract(Model(), audio, "accordion")
        self.assertEqual(len(result), len(audio))
        np.testing.assert_allclose(result, audio * 0.5, atol=1e-6)


if __name__ == "__main__":
    unittest.main()

class BassOctaveTests(unittest.TestCase):
    def test_bass_is_raised_once_in_individual_and_combined_files(self):
        import numpy as np
        import soundfile as sf
        import pretty_midi
        import audio2midi_core as core
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "bass.wav"
            sf.write(source, np.zeros(16000), 16000)
            midi = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(33)
            inst.notes = [pretty_midi.Note(90, 40, 0.1, 0.8)]
            midi.instruments.append(inst)
            with patch("sys.argv", ["core", str(source), "-o", str(root / "out"), "--no-rhythm-lock"]), \
                 patch.object(core, "prepare_wav", side_effect=lambda path, workdir: path), \
                 patch.object(core, "analyze_metronome", return_value=(120, np.array([]))), \
                 patch.object(core, "transcribe_stem", return_value=midi), \
                 patch.object(core, "validate_bass_pitch", return_value=(midi, {"checked": 0})):
                self.assertEqual(core.main(), 0)
            for name in ("bass.mid", "completo.mid"):
                result = pretty_midi.PrettyMIDI(str(root / "out/midi" / name))
                self.assertEqual([n.pitch for i in result.instruments for n in i.notes], [52])
            self.assertEqual(inst.notes[0].pitch, 40)

class SeparationCacheTests(unittest.TestCase):
    def test_only_reported_stems_are_exported_and_cache_skips_model(self):
        import json
        from instrument_separator import separate_instruments
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "other.wav").write_bytes(b"audio-source")
            def worker(command, **kwargs):
                output = Path(command[command.index("--output") + 1])
                (output / "accordion.wav").write_bytes(b"separated")
                (output / "instrumentos.json").write_text(json.dumps({"exported": ["accordion"]}))
                (output.parent / "acompanhamento_nao_identificado.wav").write_bytes(b"residual")
            with patch("instrument_separator.ensure_runtime", return_value=Path(sys.executable)) as setup:
                from unittest.mock import Mock
                runner = Mock(side_effect=worker)
                first = separate_instruments(source, root / "one/stems", root / "models", runner)
                second = separate_instruments(source, root / "two/stems", root / "models", runner)
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(setup.call_count, 1)
            self.assertTrue((second / "accordion.wav").is_file())
            self.assertFalse((second / "electric_guitar.wav").exists())
            self.assertFalse((second / "acompanhamento_nao_identificado.wav").exists())
            self.assertTrue((second.parent / "acompanhamento_nao_identificado.wav").is_file())
