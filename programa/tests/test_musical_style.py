import sys, tempfile, unittest, copy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mido, numpy as np, pretty_midi
from musical_style import read_reference, apply_style, DEFAULT_STYLE
from audio2midi_core import analyze_metronome, build_grid


def notes(items):
    m = pretty_midi.PrettyMIDI()
    i = pretty_midi.Instrument(25)
    i.notes = [pretty_midi.Note(v, p, a, b) for p, a, b, v in items]
    m.instruments = [i]
    return m


class MusicalStyleTests(unittest.TestCase):
    def test_fragment_merge_without_arbitrary_polyphony_cap(self):
        m = notes(
            [(60, 0.1, 0.16, 40), (60, 0.18, 0.28, 40)] + [(p, 0.1, 0.3, 40) for p in range(61, 70)]
        )
        result = apply_style(m, "guitar", 120, [], 0)
        self.assertEqual(len(result.instruments[0].notes), 10)
        c = next(n for n in result.instruments[0].notes if n.pitch == 60)
        self.assertAlmostEqual(c.start, 0.1)
        self.assertAlmostEqual(c.end, 0.28)
        self.assertEqual(len(m.instruments[0].notes), 11)

    def test_no_fixed_scale_without_same_song_reference(self):
        m = notes([(60, 0, 0.2, 30), (61, 0.3, 0.5, 30)])
        result = apply_style(m, "guitar", 120, [], 0)
        self.assertEqual([n.pitch for n in result.instruments[0].notes], [60, 61])
        self.assertNotIn("scale", DEFAULT_STYLE)

    def test_vocal_note_timings_and_pitches_preserved(self):
        m = notes([(60, 0.123, 0.189, 30), (60, 0.21, 0.25, 40)])
        result = apply_style(
            m, "vocals", 120, build_grid(1, 120), 16, {"scale": [1, 3, 4, 6, 8, 10, 11]}
        )
        self.assertEqual(
            [(n.pitch, n.start, n.end) for n in result.instruments[0].notes],
            [(60, 0.123, 0.189), (60, 0.21, 0.25)],
        )

    def test_reference_extracts_only_aggregate_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "guide.mid"
            m = mido.MidiFile(ticks_per_beat=220)
            m.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000)]))
            track = mido.MidiTrack(
                [
                    mido.MetaMessage("track_name", name="Bass"),
                    mido.Message("program_change", channel=0, program=33),
                ]
            )
            for j, p in enumerate((61, 63, 64, 66, 68, 70, 71)):
                track.extend(
                    [
                        mido.Message(
                            "note_on", channel=0, note=p, velocity=50, time=17 if j == 0 else 0
                        ),
                        mido.Message("note_off", channel=0, note=p, time=55),
                    ]
                )
            m.tracks.append(track)
            m.save(path)
            result = read_reference(path)
            self.assertEqual(result["scale"], [1, 3, 4, 6, 8, 10, 11])
            self.assertAlmostEqual(result["bpm"], 120)
            self.assertGreater(result["grid_confidence"], 0.99)
            self.assertNotIn("notes", result)
            self.assertEqual(result["stats"]["bass"]["program"], 33)

    def test_tempo_refined_from_beat_positions_not_fixed_to_this_song(self):
        rate = 22050
        true_bpm = 133.0
        period = 60 / true_bpm
        frames = np.rint((0.05 + period * np.arange(120)) * rate / 512).astype(int)
        with (
            patch("audio2midi_core.librosa.load", return_value=(np.zeros(100), rate)),
            patch(
                "audio2midi_core.librosa.beat.beat_track", return_value=(np.array([136.0]), frames)
            ),
        ):
            bpm, _ = analyze_metronome(None, 4, 60, reference_path=Path("fake.wav"))
        self.assertAlmostEqual(bpm, true_bpm, delta=0.1)

    def test_reference_lyrics_are_not_copied_into_generated_karaoke(self):
        import soundfile as sf
        from test_studio import fixture
        from pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reference = root / "reference.mid"
            m = fixture(reference)
            m.tracks.append(
                mido.MidiTrack([mido.MetaMessage("lyrics", text="REFERENCIA NAO COPIAR")])
            )
            m.save(reference)
            # Esta referência tem variação de andamento: não impõe BPM constante.
            stems = root / "stems"
            stems.mkdir()
            sf.write(stems / "vocals.wav", np.zeros(16000), 16000)

            def engine(command, **kwargs):
                self.assertIn("--style-profile", command)
                self.assertIn("--compact", command)
                dest = Path(command[command.index("-o") + 1]) / "midi"
                dest.mkdir(parents=True)
                fixture(dest / "completo.mid")
                return "ok"

            with patch("pipeline._run", side_effect=engine):
                result = run_pipeline(
                    root / "job",
                    uploaded_stems_dir=stems,
                    musical_reference=reference,
                    recognize_lyrics=False,
                    manual_lyrics="Minha letra",
                    grid=16,
                )
            karaoke = mido.MidiFile(result["karaoke_kar"], charset="latin1")
            text = "".join(msg.text for t in karaoke.tracks for msg in t if msg.type == "lyrics")
            self.assertIn("Minha letra", text)
            self.assertNotIn("REFERENCIA", text)

    def test_default_engine_uses_compact_guitar_and_sustained_polyphony(self):
        import soundfile as sf
        import audio2midi_core as core

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stems = root / "stems"
            stems.mkdir()
            output = root / "out"
            for name in ("bass", "other"):
                sf.write(stems / (name + ".wav"), np.zeros(16000), 16000)

            def predict(path, stem):
                if stem == "bass":
                    return notes([(40, 0.1, 0.4, 40), (52, 0.1, 0.4, 40)])
                self.assertEqual(stem, "guitar")
                return notes([(60, 0.1, 0.16, 40), (60, 0.18, 0.28, 40)])

            with (
                patch(
                    "sys.argv",
                    ["core", str(stems), "-o", str(output), "--compact", "--no-rhythm-lock"],
                ),
                patch.object(core, "prepare_wav", side_effect=lambda path, workdir: path),
                patch.object(core, "analyze_metronome", return_value=(120, np.array([]))),
                patch.object(core, "transcribe_stem", side_effect=predict),
            ):
                self.assertEqual(core.main(), 0)
            out = pretty_midi.PrettyMIDI(str(output / "midi/completo.mid"))
            self.assertEqual({i.name for i in out.instruments}, {"Bass", "Guitar"})
            self.assertEqual(len(next(i for i in out.instruments if i.name == "Bass").notes), 2)
            self.assertEqual(len(next(i for i in out.instruments if i.name == "Guitar").notes), 1)

    def test_short_high_frequency_attacks_are_hihat_without_repeated_spikes(self):
        import soundfile as sf
        from audio2midi_core import transcribe_drums

        rate = 22050
        rng = np.random.default_rng(31)
        y = np.zeros(rate * 2)
        size = int(rate * 0.03)
        noise = rng.normal(size=size)
        fft = np.fft.rfft(noise)
        freq = np.fft.rfftfreq(size, 1 / rate)
        fft[(freq < 6000) | (freq > 10000)] = 0
        hit = np.fft.irfft(fft, n=size) * np.hanning(size)
        for start in (0.3, 0.6, 0.9, 0.92):
            index = int(start * rate)
            y[index : index + size] += hit
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drums.wav"
            sf.write(path, y, rate)
            midi = transcribe_drums(path, 120, np.array([]), grid=0)
        hats = sorted(n.start for i in midi.instruments for n in i.notes if n.pitch == 42)
        self.assertGreaterEqual(len(hats), 3)
        self.assertTrue(all(b - a >= 0.065 for a, b in zip(hats, hats[1:])))

    def test_approved_karaoke_words_and_seconds_survive_tempo_change(self):
        from lyrics import add_lyrics_track, export_karaoke, read_timed_lyrics
        from test_studio import fixture

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "old.mid"
            fixture(old, ppq=960)
            add_lyrics_track(old, [(0.5, "sí"), (1.5, "laba"), (2.0, "\r\n")], 120, old)
            rows = read_timed_lyrics(old.read_bytes())
            base = mido.MidiFile(type=1, ticks_per_beat=480)
            base.tracks.append(
                mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(158))])
            )
            path = root / "new.mid"
            base.save(path)
            export_karaoke(path, rows, root)
            restored = read_timed_lyrics(root / "karaoke.kar")
            self.assertEqual([r["text"] for r in rows], [r["text"] for r in restored])
            self.assertLess(
                max(abs(a["seconds"] - b["seconds"]) for a, b in zip(rows, restored)), 0.001
            )
