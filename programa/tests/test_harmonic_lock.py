import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mido
from harmonic_lock import lock_midi, lock_result
from accompaniment_cleanup import key_scale
from music_guide import chord_notes
from sample_audio import start_midi_playback


def instrument(channel, program=0, notes=(61, 66, 70)):
    track = mido.MidiTrack([mido.MetaMessage('track_name', name='instrument'),
                           mido.Message('program_change', channel=channel, program=program)])
    for note in notes:
        track.append(mido.Message('note_on', channel=channel, note=note, velocity=90, time=120))
        track.append(mido.Message('note_off', channel=channel, note=note, time=240))
    return track


class HarmonyTests(unittest.TestCase):
    def test_every_channel_and_program_except_drums(self):
        midi = mido.MidiFile()
        for channel in range(16):
            midi.tracks.append(instrument(channel, [0, 33, 21, 27, 73][channel % 5]))
        drums = copy.deepcopy(midi.tracks[9])
        original_times = [[m.time for m in t] for t in midi.tracks]
        report = lock_midi(midi, 'C maior', [(0, 'D7')])
        allowed_tones = key_scale('C maior') | {n % 12 for n in chord_notes('D7')}
        for track in midi.tracks:
            for msg in track:
                if msg.type in ('note_on', 'note_off') and msg.channel != 9:
                    self.assertIn(msg.note % 12, allowed_tones)
        self.assertEqual(midi.tracks[9], drums)
        self.assertEqual([[m.time for m in t] for t in midi.tracks], original_times)
        self.assertEqual(report['chords_outside_scale'], ['D7'])

    def test_different_solos_inside_scale_are_unchanged(self):
        midi = mido.MidiFile()
        midi.tracks.extend([instrument(1, 21, (60, 62, 64, 65)), instrument(2, 27, (69, 67, 65, 64))])
        original = copy.deepcopy(midi.tracks)
        lock_midi(midi, 'C maior', [(0, 'C')])
        self.assertEqual(midi.tracks, original)

    def test_note_off_follows_note_on_after_chord_change(self):
        midi = mido.MidiFile()
        midi.tracks.append(mido.MidiTrack([
            mido.Message('note_on', channel=1, note=61, velocity=90),
            mido.Message('note_on', channel=1, note=61, velocity=70, time=480),
            mido.Message('note_off', channel=1, note=61, time=480),
            mido.Message('note_on', channel=1, note=61, velocity=0, time=480)]))
        lock_midi(midi, 'C maior', [(0, 'C'), (0.5, 'Dm')])
        self.assertEqual([msg.note for msg in midi.tracks[0]], [60, 62, 60, 62])

    def test_all_keys_and_pitch_boundaries(self):
        for key in ('C maior', 'F# maior', 'Bb maior', 'A menor', 'Eb menor'):
            midi = mido.MidiFile()
            midi.tracks.append(instrument(0, 33, range(128)))
            lock_midi(midi, key)
            self.assertTrue(all(msg.note % 12 in key_scale(key) for msg in midi.tracks[0] if msg.type in ('note_on', 'note_off')))

    def test_pitch_bends_neutralized_except_drums(self):
        midi = mido.MidiFile()
        midi.tracks.append(mido.MidiTrack([mido.Message('pitchwheel', channel=0, pitch=4096),
                                          mido.Message('pitchwheel', channel=9, pitch=4096)]))
        lock_midi(midi, 'A menor')
        self.assertEqual([msg.pitch for msg in midi.tracks[0]], [0, 4096])

    def test_individual_and_combined_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); folder = root / 'midi'; folder.mkdir()
            for name in ('completo', 'bass', 'vocals', 'accordion'):
                midi = mido.MidiFile(); midi.tracks.append(instrument(0)); midi.save(folder / (name + '.mid'))
            report = lock_result(folder, root, 'D maior', [(0, 'D')])
            self.assertEqual(len(report['files']), 4)
            self.assertEqual(json.loads((root/'relatorio_harmonia.json').read_text())['key'], 'D maior')
            for file in folder.glob('*.mid'):
                self.assertTrue(all(msg.note % 12 in key_scale('D maior') for track in mido.MidiFile(file).tracks
                                    for msg in track if msg.type == 'note_on'))

    def test_midi_player_loads_bank_and_original_midi(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); midi = root/'song.mid'; bank = root/'bank.sf2'
            midi.touch(); bank.touch()
            with patch('sample_audio.synthesizer', return_value='fluidsynth.exe'), patch('sample_audio.SOUNDFONT', bank), patch('sample_audio.subprocess.Popen') as start:
                start_midi_playback(midi, root/'player.log')
                args = start.call_args.args[0]
                self.assertEqual(args[-2:], [str(bank), str(midi)])
                self.assertNotIn('-F', args)

    def test_pipeline_scale_survives_kar_export_and_remix(self):
        import numpy as np
        import soundfile as sf
        from pipeline import run_pipeline, remix_result
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); audio = root/'audio.wav'; sf.write(audio, np.zeros(64000), 16000)
            def engine(command, **kwargs):
                output = Path(command[command.index('-o')+1])/'midi'; output.mkdir(parents=True)
                midi = mido.MidiFile()
                for channel, program in ((0,33),(1,73),(2,0),(5,21),(9,0)):
                    midi.tracks.append(instrument(channel, program))
                midi.save(output/'completo.mid')
                return 'ok'
            with patch('shutil.which', return_value='ffmpeg.exe'), patch('pipeline._run', side_effect=engine):
                result = run_pipeline(root/'job', audio_path=audio, separate=False, recognize_lyrics=False,
                    manual_lyrics='[00:00 D7] Minha canção', bpm_override=120, tonality='C maior',
                    accompaniment_filter='off', harmonic_correction=False, chord_accompaniment=True)
            remix_result(result, {'bass':120})
            for file in Path(result['midi_dir']).iterdir():
                if file.suffix not in ('.mid','.kar'): continue
                for track in mido.MidiFile(file, charset='latin1').tracks:
                    for msg in track:
                        if msg.type == 'note_on' and msg.velocity and msg.channel != 9:
                            self.assertIn(msg.note % 12, key_scale('C maior') | {n % 12 for n in chord_notes('D7')}, file.name)
