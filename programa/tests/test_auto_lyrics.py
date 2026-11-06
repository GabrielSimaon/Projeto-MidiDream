import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mido
import numpy as np
import soundfile as sf

from lyrics_only import align_and_insert_lyrics
from test_updates_36 import musical_events
from midi_timing import TempoMap


class AutoLyricsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source, self.output = self.root / "base.mid", self.root / "novo.mid"
        self.audio = self.root / "reference.wav"
        sf.write(self.audio, np.zeros(16000 * 12), 16000)
        self.midi = mido.MidiFile(ticks_per_beat=480)
        self.midi.tracks.append(mido.MidiTrack([
            mido.MetaMessage("set_tempo", tempo=500000),
            mido.Message("program_change", program=33, channel=0),
            mido.Message("note_on", note=40, velocity=90, time=480),
            mido.MetaMessage("lyrics", text="antiga", time=480),
            mido.MetaMessage("set_tempo", tempo=1000000, time=960),
            mido.Message("note_off", note=40, time=4800),
        ]))
        self.midi.save(self.source)
        self.words = [{"text": "Amor", "start": 2.0, "end": 2.7},
                      {"text": "Amor", "start": 8.0, "end": 8.7}]

    def tearDown(self):
        self.folder.cleanup()

    def test_audio_alignment_keeps_refrain_pause_text_and_all_musical_events(self):
        with patch("pipeline.transcribe_words", return_value=self.words) as align:
            result = align_and_insert_lyrics(self.source, self.output, self.audio, "Amor\nAmor")
        self.assertEqual(align.call_args.kwargs["alignment_text"], "Amor\nAmor")
        self.assertEqual(result["words"], 2)
        output = mido.MidiFile(self.output)
        self.assertEqual(musical_events(self.midi), musical_events(output))
        timing = TempoMap(output)
        tick, starts = 0, []
        for msg in output.tracks[-1]:
            tick += msg.time
            if msg.type == "lyrics" and msg.text == "Amor":
                starts.append(timing.ticks_to_seconds(tick))
        self.assertEqual(starts, [2.0, 8.0])

    def test_offset_and_out_of_range_are_not_silently_truncated(self):
        with patch("pipeline.transcribe_words", return_value=self.words):
            align_and_insert_lyrics(self.source, self.output, self.audio, "Amor\nAmor", offset=0.5)
            with self.assertRaisesRegex(ValueError, "fora da duração"):
                align_and_insert_lyrics(self.source, self.output, self.audio, "Amor\nAmor", offset=20)

    def test_no_words_and_bad_timestamps_fail_without_writing_output(self):
        for words in ([], [{"text": "Amor", "start": float("nan"), "end": 2}]):
            with patch("pipeline.transcribe_words", return_value=words):
                with self.assertRaises(ValueError):
                    align_and_insert_lyrics(self.source, self.output, self.audio, "Amor")
            self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
