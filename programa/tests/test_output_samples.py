import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from output_settings import load_destination, save_destination, save_lyric_files
from sample_audio import SOUNDFONT, render_result, render_midi


class OutputTests(unittest.TestCase):
    def test_destination_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'settings.json'
            self.assertEqual(load_destination(config, root), str(root))
            destination = save_destination(config, str(root / 'Músicas'))
            self.assertEqual(load_destination(config, root), str(destination))
            first = save_lyric_files(destination, 'canção', b'first')
            second = save_lyric_files(destination, 'canção', b'second')
            self.assertNotEqual(first, second)
            self.assertEqual((first / 'canção.mid').read_bytes(), b'first')
            self.assertEqual((second / 'canção.kar').read_bytes(), b'second')
            with self.assertRaises(ValueError):
                save_destination(config, 'relative')

    def test_bank_integrity_and_full_gm_coverage(self):
        raw = SOUNDFONT.read_bytes()
        manifest = json.loads(SOUNDFONT.with_name('manifest.json').read_text())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest['sha256'])
        offset = raw.index(b'phdr')
        size = struct.unpack_from('<I', raw, offset + 4)[0]
        presets = [struct.unpack_from('<20sHHHIII', raw, i)
                   for i in range(offset + 8, offset + 8 + size - 38, 38)]
        self.assertEqual({p[1] for p in presets if p[2] == 0}, set(range(128)))
        self.assertTrue(any(p[2] == 128 for p in presets))

    def test_failed_remix_removes_stale_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'midi').mkdir()
            (root / 'midi/bass.mid').touch()
            (root / 'audio_samples').mkdir()
            stale = root / 'audio_samples/bass.mp3'
            stale.write_bytes(b'old mix')
            with patch('sample_audio.render_midi', side_effect=RuntimeError('missing bank')):
                result = render_result(root)
            self.assertFalse(stale.exists())
            self.assertEqual(len(result['errors']), 1)
            self.assertTrue((root / 'midi/bass.mid').exists())

    def test_real_render_instruments_and_drums(self):
        import mido
        import soundfile as sf
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, program, channel, note in [('piano', 0, 0, 60), ('sanfona', 21, 1, 60),
                    ('violao', 24, 2, 60), ('guitarra', 27, 3, 60), ('baixo', 33, 4, 48),
                    ('bateria', 0, 9, 36), ('voz', 73, 5, 72)]:
                midi = mido.MidiFile()
                track = mido.MidiTrack(); midi.tracks.append(track)
                track.append(mido.Message('program_change', channel=channel, program=program))
                track.append(mido.Message('note_on', channel=channel, note=note, velocity=100))
                track.append(mido.Message('note_off', channel=channel, note=note, time=480))
                source = root / (name + '.mid'); midi.save(source)
                target = render_midi(source, root / (name + '.wav'))
                data, rate = sf.read(target)
                self.assertEqual(rate, 44100)
                self.assertGreater(abs(data).max(), 0.001, name)
                self.assertLess(abs(data).max(), 1, name)
            mp3 = render_midi(source, root / 'voice.mp3')
            self.assertGreater(mp3.stat().st_size, 1000)
