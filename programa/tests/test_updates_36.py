import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mido
import numpy as np
import pretty_midi
import soundfile as sf

from bass_pitch import validate_bass_pitch
from lyrics_only import insert_lyrics, lyric_lines
from midi_timing import TempoMap
from note_cleanup import clean_notes
from pipeline import download_youtube


def musical_events(midi):
    events = []
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if not message.is_meta or message.type in ("set_tempo", "time_signature", "key_signature"):
                events.append((tick, message.copy(time=0)))
    return events


class Updates36Tests(unittest.TestCase):
    def test_lyrics_preserve_mixed_type_zero_notes_controllers_and_tempos(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "original.mid", Path(folder) / "novo.mid"
            midi = mido.MidiFile(type=0, ticks_per_beat=960)
            midi.tracks.append(mido.MidiTrack([
                mido.MetaMessage("track_name", name="Lyrics"),
                mido.Message("program_change", program=33, channel=4),
                mido.MetaMessage("set_tempo", tempo=500000),
                mido.Message("note_on", channel=4, note=40, velocity=90, time=960),
                mido.MetaMessage("lyrics", text="antiga", time=240),
                mido.Message("control_change", channel=4, control=64, value=127, time=240),
                mido.MetaMessage("set_tempo", tempo=1000000, time=480),
                mido.MetaMessage("text", text="/letra antiga", time=100),
                mido.Message("note_off", channel=4, note=40, time=1820),
            ]))
            midi.save(source)
            insert_lyrics(source, output, [{"inicio": 1.5, "fim": 2.5, "texto": "coração"}])
            result = mido.MidiFile(output, charset="latin1")
            self.assertEqual(musical_events(midi), musical_events(result))
            self.assertEqual(result.ticks_per_beat, 960)
            tick = 0
            lyric_tick = None
            for msg in result.tracks[-1]:
                tick += msg.time
                if msg.type == "lyrics":
                    lyric_tick = tick
                    break
            self.assertEqual(lyric_tick, TempoMap(midi).seconds_to_ticks(1.5))
            self.assertFalse(any(m.type == "lyrics" for m in result.tracks[0]))
            insert_lyrics(output, output, lyric_lines("nova letra", 0.5, 2.5))
            repeated = mido.MidiFile(output)
            self.assertEqual(musical_events(midi), musical_events(repeated))
            self.assertEqual(len(repeated.tracks), len(result.tracks))

    def test_lyrics_invalid_times_and_missing_text(self):
        with self.assertRaises(ValueError):
            lyric_lines("", 0, 10)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "a.mid"
            midi = mido.MidiFile()
            midi.tracks.append(mido.MidiTrack([mido.MetaMessage("end_of_track", time=4800)]))
            midi.save(source)
            for start, end in [(-1, 2), (1, 0), (0, float("nan")), (0, 999)]:
                with self.assertRaises(ValueError):
                    insert_lyrics(source, source, [{"inicio": start, "fim": end, "texto": "oi"}])

    def test_bass_corrects_semitone_and_octave_from_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            sr = 8000
            t = np.arange(sr * 3) / sr
            frequency = 440 * 2 ** ((40 - 69) / 12)
            audio = 0.5 * np.sin(2 * np.pi * frequency * t) + 0.15 * np.sin(4 * np.pi * frequency * t)
            wav = Path(folder) / "bass.wav"
            sf.write(wav, audio, sr)
            midi = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(33)
            inst.notes = [pretty_midi.Note(90, 41, 0.25, 1.2), pretty_midi.Note(80, 52, 1.4, 2.7)]
            midi.instruments.append(inst)
            corrected, report = validate_bass_pitch(midi, wav)
            self.assertEqual([n.pitch for n in corrected.instruments[0].notes], [40, 40])
            self.assertEqual([n.pitch for n in inst.notes], [41, 52])
            self.assertEqual(report["corrected"], 2)
            corrected, _ = clean_notes(corrected, "bass", mode="auto")
            self.assertEqual(len(corrected.instruments[0].notes), 2)

    def test_uncertain_bass_audio_does_not_force_scale(self):
        midi = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(33)
        inst.notes = [pretty_midi.Note(90, 42, 0.1, 1.0)]
        midi.instruments.append(inst)
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / "silence.wav"
            sf.write(wav, np.zeros(16000), 8000)
            result, report = validate_bass_pitch(midi, wav)
        self.assertEqual(result.instruments[0].notes[0].pitch, 42)
        self.assertEqual(report["corrected"], 0)

    def test_piano_removes_short_chromatic_fragment(self):
        from accompaniment_cleanup import filter_midi
        from test_accompaniment_cleanup import fixture
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "piano.mid"
            fixture(source, [(60, 0, 1920, 100), (61, 300, 530, 45)])
            report = filter_midi(source, source, key="C maior", level="balanced")
            self.assertEqual(report["removed"], 1)
            self.assertTrue(any(change["reason"] == "dissonant_fragment" for change in report["changes"]))

    def test_youtube_repairs_signature_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "musica.wav").touch()
            with patch("pipeline._run", side_effect=[RuntimeError("signature extraction failed"), "ok"]) as run, \
                    patch("pipeline.update_youtube_downloader") as repair:
                download_youtube("youtu.be/Y0pdQU87dc8", root)
            self.assertEqual(run.call_count, 2)
            repair.assert_called_once()
            command = run.call_args.args[0]
            self.assertIn("--socket-timeout", command)
            self.assertIn("no-certifi", command)


if __name__ == "__main__":
    unittest.main()
