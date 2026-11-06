"""Testes temporais e de integração sem baixar modelos de IA."""

import json
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

from audio2midi_core import post_process_notes, transcribe_drums, scan_audio_files
from lyrics import add_lyrics_track, export_karaoke, remap_channels
from midi_timing import TempoMap, rescale_ppq, make_instrumental
from pipeline import run_pipeline, revise_lyrics, download_youtube


def fixture(path, ppq=480):
    midi = mido.MidiFile(type=1, ticks_per_beat=ppq)
    midi.tracks.append(
        mido.MidiTrack(
            [
                mido.MetaMessage("set_tempo", tempo=500000),
                mido.MetaMessage("set_tempo", tempo=1000000, time=ppq * 2),
            ]
        )
    )
    for name, channel, pitch in [
        ("Bass", 3, 40),
        ("Choir Aahs", 4, 60),
        ("Drums", 5, 36),
        ("Other", 6, 64),
    ]:
        midi.tracks.append(
            mido.MidiTrack(
                [
                    mido.MetaMessage("track_name", name=name),
                    mido.Message("program_change", channel=channel, program=0),
                    mido.Message("note_on", channel=channel, note=pitch, velocity=90, time=ppq),
                    mido.Message("note_off", channel=channel, note=pitch, time=ppq * 2),
                ]
            )
        )
    midi.save(path)
    return midi


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mid = self.root / "source.mid"
        fixture(self.mid)

    def tearDown(self):
        self.temp.cleanup()

    def test_tempo_changes(self):
        mapping = TempoMap(mido.MidiFile(self.mid))
        self.assertEqual(mapping.seconds_to_ticks(0.5), 480)
        self.assertEqual(mapping.seconds_to_ticks(1.5), 1200)
        add_lyrics_track(self.mid, [(1.5, "ção"), (2, "\r\n")], 999, self.mid)
        absolute = 0
        events = []
        for msg in mido.MidiFile(self.mid).tracks[-1]:
            absolute += msg.time
            if msg.type == "lyrics":
                events.append((absolute, msg.text))
        self.assertEqual(events, [(1200, "ção"), (1440, "\r\n")])

    def test_rescale_has_no_cumulative_drift(self):
        midi = mido.MidiFile(ticks_per_beat=960)
        midi.tracks.append(
            mido.MidiTrack([mido.MetaMessage("marker", text="x", time=1) for _ in range(1001)])
        )
        rescale_ppq(midi, 480)
        self.assertEqual(sum(m.time for m in midi.tracks[0]), round(1001 / 2))
        self.assertEqual(midi.type, 1)

    def test_channels_and_instrumental(self):
        remap_channels(self.mid)
        midi = mido.MidiFile(self.mid)
        channels = [next(m.channel for m in t if m.type == "note_on") for t in midi.tracks[1:]]
        self.assertEqual(channels, [0, 1, 9, 4])
        output = self.root / "instrumental.mid"
        make_instrumental(self.mid, output)
        self.assertFalse(
            any(
                m.type == "note_on" and m.channel == 1
                for t in mido.MidiFile(output).tracks
                for m in t
            )
        )
        self.assertTrue(any(m.type == "set_tempo" for t in mido.MidiFile(output).tracks for m in t))

    def test_karaoke_reexport_crlf_no_bars(self):
        rows = [{"seconds": 0.5, "text": "ol/á\\"}, {"seconds": 1, "text": "[QUEBRA]"}]
        export_karaoke(self.mid, rows, self.root)
        export_karaoke(self.root / "karaoke.mid", rows, self.root)
        midi = mido.MidiFile(self.root / "karaoke.kar")
        texts = [m.text for t in midi.tracks for m in t if m.type == "lyrics"]
        self.assertEqual(texts, ["olá", "\r\n"])
        self.assertEqual(midi.type, 1)

    def test_invalid_lyric_time(self):
        for seconds in [-1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                export_karaoke(self.mid, [{"seconds": seconds, "text": "a"}], self.root)

    def test_unquantized_notes_and_repeated_attack(self):
        midi = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(0)
        inst.notes = [
            pretty_midi.Note(90, 60, 0.123, 0.251),
            pretty_midi.Note(85, 60, 0.271, 0.401),
        ]
        midi.instruments.append(inst)
        result = post_process_notes(midi, "piano", 120, np.array([]), grid=0)
        self.assertEqual(
            [(n.start, n.end) for n in result.instruments[0].notes],
            [(0.123, 0.251), (0.271, 0.401)],
        )

    def test_silent_drums(self):
        wav = self.root / "drums.wav"
        sf.write(wav, np.zeros(22050), 22050)
        midi = transcribe_drums(wav, 120, np.array([]), grid=0)
        self.assertEqual(sum(len(i.notes) for i in midi.instruments), 0)

    def test_file_input_does_not_scan_neighbors(self):
        source = self.root / "song.opus"
        source.touch()
        (self.root / "unrelated.wav").touch()
        self.assertEqual(scan_audio_files(source, self.root / "out"), [source])

    def test_youtube_host_validation(self):
        with self.assertRaises(ValueError):
            download_youtube("https://youtube.com.evil.test/watch?v=1", self.root)

    def test_pipeline_and_revision_with_stubbed_inference(self):
        audio = self.root / "song.wav"
        sf.write(audio, np.zeros(22050 * 3), 22050)
        job = self.root / "job"

        def fake_core(cmd, **kwargs):
            target = Path(cmd[cmd.index("-o") + 1]) / "midi"
            target.mkdir(parents=True)
            fixture(target / "completo.mid")
            self.assertEqual(cmd[2], str(audio))  # Somente este arquivo, não sua pasta.
            return "inferencia simulada para teste estrutural"

        with (
            patch("pipeline._run", side_effect=fake_core),
            patch(
                "pipeline.transcribe_words",
                return_value=[
                    {"text": "Olá", "start": 0.5, "end": 1},
                    {"text": "mundo", "start": 1.5, "end": 2},
                ],
            ),
        ):
            result = run_pipeline(job, audio_path=audio, separate=False, ppq=960, vocal_program=73)
        self.assertTrue(result["archive"].exists())
        midi = mido.MidiFile(result["karaoke_mid"])
        self.assertEqual(midi.ticks_per_beat, 960)
        self.assertTrue(
            any(
                m.type == "program_change" and m.channel == 1 and m.program == 73
                for t in midi.tracks
                for m in t
            )
        )
        revise_lyrics(
            result, [{"seconds": 1.5, "text": "ação"}, {"seconds": 2, "text": "[QUEBRA]"}]
        )
        self.assertEqual(json.loads(result["events_path"].read_text())[0]["text"], "ação")
        self.assertFalse(
            any(m.type == "lyrics" for t in mido.MidiFile(result["combined_mid"]).tracks for m in t)
        )


if __name__ == "__main__":
    unittest.main()
