import sys, tempfile, unittest, json, types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mido
import numpy as np
import soundfile as sf
from midi_mix import mix_midi, apply_mix, validate_mix
from pipeline import revise_lyrics, run_pipeline
from lyrics import transcribe_words, build_lyric_events
from test_studio import fixture


def mix_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=960)
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=600000)]))
    for ch, pitch in [(0, 40), (9, 36), (9, 38), (9, 42), (9, 49), (2, 60), (4, 64), (1, 67)]:
        midi.tracks.append(
            mido.MidiTrack(
                [
                    mido.Message("program_change", channel=ch, program=0),
                    mido.Message("note_on", channel=ch, note=pitch, velocity=80, time=120),
                    mido.Message("note_off", channel=ch, note=pitch, time=360),
                ]
            )
        )
    midi.save(path)
    return midi


class MixTests(unittest.TestCase):
    def test_bass_and_all_requested_drum_pieces_louder(self):
        with tempfile.TemporaryDirectory() as folder:
            midi = mix_fixture(Path(folder) / "base.mid")
            mix_midi(midi, {"other": 0})
            for track in midi.tracks:
                if len(track) < 2:
                    continue
                note = next(m for m in track if m.type == "note_on")
                if note.channel in (0, 9):
                    self.assertGreater(note.velocity, 80)
                    self.assertGreater(
                        next(
                            m.value for m in track if m.type == "control_change" and m.control == 7
                        ),
                        100,
                    )
                else:
                    self.assertEqual(note.velocity, 80)
                self.assertEqual(sum(m.time for m in track), 480)
            other = midi.tracks[7]
            self.assertEqual(
                next(m.value for m in other if m.type == "control_change" and m.control == 7), 0
            )

    def test_other_is_audible_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            midi = mix_fixture(Path(folder) / "base.mid")
            mix_midi(midi, {})
            other = midi.tracks[7]
            self.assertFalse(
                any(m.type == "control_change" and m.channel == 4 and m.value == 0 for m in other)
            )

    def test_repeat_remix_does_not_accumulate_gain_and_can_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "midi").mkdir()
            mix_fixture(root / "midi/completo.mid")
            apply_mix(root, {})
            first = (root / "midi/completo.mid").read_bytes()
            apply_mix(root, {})
            self.assertEqual(first, (root / "midi/completo.mid").read_bytes())
            apply_mix(
                root,
                {
                    "bass": 100,
                    "drums": 100,
                    "kick": 100,
                    "snare": 100,
                    "hihat": 100,
                    "cymbals": 100,
                    "mute_other": False,
                    "punch": 0,
                },
            )
            midi = mido.MidiFile(root / "midi/completo.mid")
            self.assertTrue(
                all(m.velocity == 80 for t in midi.tracks for m in t if m.type == "note_on")
            )
            self.assertFalse(
                any(
                    m.type == "control_change" and m.channel == 4 and m.value == 0
                    for t in midi.tracks
                    for m in t
                )
            )

    def test_new_lyrics_survive_later_remix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result_dir = root / "resultados"
            (result_dir / "midi").mkdir(parents=True)
            mix_fixture(result_dir / "midi/completo.mid")
            values = apply_mix(result_dir, {})
            result = {
                "job_dir": root,
                "result_dir": result_dir,
                "midi_dir": result_dir / "midi",
                "events_path": result_dir / "silabas.json",
                "stems_dir": root / "no_stems",
                "include_stems": False,
                "mix_settings": values,
            }
            revise_lyrics(result, [{"seconds": 0.2, "text": "certo "}])
            apply_mix(result_dir, {"bass": 120})
            midi = mido.MidiFile(result_dir / "midi/karaoke.kar", charset="latin1")
            self.assertIn("certo ", [m.text for t in midi.tracks for m in t if m.type == "lyrics"])

    def test_piano_isolation_is_not_overridden_by_fast_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "song.wav"
            sf.write(source, np.zeros(16000), 16000)
            stems = root / "stems"
            stems.mkdir()
            for name in ("bass", "drums", "other", "vocals", "piano", "guitar"):
                sf.write(stems / (name + ".wav"), np.zeros(16000), 16000)

            def engine(cmd, **kw):
                dest = Path(cmd[cmd.index("-o") + 1]) / "midi"
                dest.mkdir(parents=True)
                fixture(dest / "completo.mid")
                return "ok"

            with (
                patch("pipeline.separate_stems", return_value=stems) as separate,
                patch("pipeline._run", side_effect=engine),
            ):
                run_pipeline(
                    root / "job",
                    audio_path=source,
                    speed="fast",
                    isolate_piano=True,
                    recognize_lyrics=False,
                )
                self.assertEqual(separate.call_args.kwargs["model"], "htdemucs_6s")
                self.assertEqual(separate.call_args.kwargs["speed"], "balanced")


class LyricQualityTests(unittest.TestCase):
    def test_uncertain_silence_skipped_without_dropping_valid_words(self):
        args = {}

        def model(*a, **kw):
            def transcribe(*a, **kw):
                args.update(kw)
                word = types.SimpleNamespace(word=" correto", start=1.0, end=1.4, probability=0.9)
                valid = types.SimpleNamespace(
                    start=1.0, end=1.4, words=[word], no_speech_prob=0.1, avg_logprob=-0.2
                )
                fake = types.SimpleNamespace(
                    start=2.0, end=3.0, words=[word], no_speech_prob=0.9, avg_logprob=-2.0
                )
                return iter([valid, fake]), types.SimpleNamespace(duration=3.0, language="pt")

            return types.SimpleNamespace(transcribe=transcribe)

        module = types.SimpleNamespace(WhisperModel=model)
        with patch.dict(sys.modules, {"faster_whisper": module}):
            words = transcribe_words("unused", fast=False)
        self.assertEqual([w["text"] for w in words], ["correto"])
        self.assertEqual(args["beam_size"], 5)
        events, plain, _ = build_lyric_events(
            detected_words=words, manual_text="Minha letra correta", duration=3, language="pt"
        )
        self.assertEqual(plain.strip(), "Minha letra correta")


class OtherLevelTests(unittest.TestCase):
    """Piano/acompanhamento ("Outros"): audível por padrão, nível ajustável."""

    def _track(self, midi, channel):
        return next(
            t for t in midi.tracks if any(not m.is_meta and m.channel == channel for m in t)
        )

    def test_default_is_audible_and_legacy_flag_still_works(self):
        self.assertEqual(validate_mix({})["other"], 100)
        self.assertFalse(validate_mix({})["mute_other"])
        self.assertEqual(validate_mix({"mute_other": True})["other"], 0)
        self.assertEqual(validate_mix({"mute_other": False})["other"], 100)

    def test_level_scales_velocity_and_zero_mutes_without_dropping_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.mid"
            mix_fixture(path)
            loud = mix_midi(mido.MidiFile(path), {"other": 150})
            quiet = mix_midi(mido.MidiFile(path), {"other": 0})
            base = mido.MidiFile(path)
            ref = [m.velocity for m in self._track(base, 4) if m.type == "note_on"]
            got = [m.velocity for m in self._track(loud, 4) if m.type == "note_on"]
            self.assertTrue(all(g == min(127, round(r * 1.5)) for g, r in zip(got, ref)))
            muted = self._track(quiet, 4)
            self.assertTrue(any(m.type == "control_change" and m.value == 0 for m in muted))
            self.assertEqual(
                [m.velocity for m in muted if m.type == "note_on"], ref
            )
