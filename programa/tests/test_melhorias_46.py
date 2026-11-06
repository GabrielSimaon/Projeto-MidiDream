"""Testes unitários das melhorias 4.6:
1. Detecção automática de BPM com alta precisão.
2. BPM opcional na interface e validação.
3. Remoção de vazamento / bleed de instrumentos de fundo.
4. Preservação de notas rápidas e ritmo (sem fusão de notas consecutivas).
5. Fidelidade harmônica às cifras (respeito a acordes com acidentes, inversões e notas fundamentais).
6. Bateria 100% preservada sem alteração de notas na harmonização.
"""

import sys
import unittest
import tempfile
from pathlib import Path
import numpy as np
import soundfile as sf
import pretty_midi
import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bpm_detector import detect_precise_bpm
from music_guide import validate_required_inputs, chord_notes, chord_root_and_bass
from bleed_cleaner import clean_midi_bleed
from harmonic_lock import lock_midi
from musical_style import apply_style
from bass_pitch import validate_bass_pitch
from drum_timing import quantize_drums


class Melhorias46Tests(unittest.TestCase):
    def test_validate_required_inputs_accepts_none_bpm(self):
        """BPM deve ser opcional: se None ou vazio, não levanta exceção."""
        guide = "G     D\nHoje eu voltei no passado\nEm7   C9\nE lembrei"
        res = validate_required_inputs(guide, None)
        self.assertIsNone(res)
        res2 = validate_required_inputs(guide, "128")
        self.assertEqual(res2, 128.0)
        with self.assertRaises(ValueError):
            validate_required_inputs(guide, "10")

    def test_bpm_detector_on_synthetic_pulse(self):
        """Detecção de BPM identifica corretamente pulso regular em áudio sintético."""
        sr = 22050
        duration = 8.0
        audio = np.zeros(int(sr * duration), dtype=np.float32)
        click = np.sin(2 * np.pi * 1000 * np.arange(int(sr * 0.015)) / sr)
        for t in np.arange(0.25, duration - 0.2, 0.5):
            idx = int(t * sr)
            audio[idx : idx + len(click)] += click

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test_drums.wav"
            sf.write(str(wav_path), audio, sr)
            info = detect_precise_bpm(wav_path)
            self.assertIn("bpm", info)
            self.assertTrue(115.0 <= info["bpm"] <= 125.0 or 58.0 <= info["bpm"] <= 62.0)

    def test_bleed_cleaner_removes_vocal_and_bass_leakage(self):
        """Notas de voz ou baixo que sangram fracas no piano/guitarra devem ser removidas."""
        vocal_midi = pretty_midi.PrettyMIDI(initial_tempo=120)
        vocal_inst = pretty_midi.Instrument(52, name="Vocals")
        vocal_inst.notes.append(pretty_midi.Note(velocity=95, pitch=67, start=1.0, end=1.8))
        vocal_midi.instruments.append(vocal_inst)

        bass_midi = pretty_midi.PrettyMIDI(initial_tempo=120)
        bass_inst = pretty_midi.Instrument(33, name="Bass")
        bass_inst.notes.append(pretty_midi.Note(velocity=90, pitch=28, start=1.0, end=2.0))
        bass_midi.instruments.append(bass_inst)

        piano_midi = pretty_midi.PrettyMIDI(initial_tempo=120)
        piano_inst = pretty_midi.Instrument(0, name="Piano")
        piano_inst.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=1.0, end=2.0))
        piano_inst.notes.append(pretty_midi.Note(velocity=65, pitch=67, start=1.05, end=1.75))
        piano_inst.notes.append(pretty_midi.Note(velocity=55, pitch=28, start=1.02, end=1.9))
        piano_midi.instruments.append(piano_inst)

        midis = {"vocals": vocal_midi, "bass": bass_midi, "piano": piano_midi}
        removed = clean_midi_bleed(midis)

        self.assertEqual(removed["piano"], 2)
        remaining_pitches = [n.pitch for n in piano_midi.instruments[0].notes]
        self.assertEqual(remaining_pitches, [60])

    def test_harmonic_lock_preserves_chord_accidentals(self):
        """Acordes com acidentes (ex: E maior com G# em tom de C maior) devem preservar o G#."""
        midi = mido.MidiFile(type=1)
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name="Piano", time=0))
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
        track.append(mido.Message("note_on", channel=1, note=68, velocity=80, time=0))
        track.append(mido.Message("note_off", channel=1, note=68, velocity=0, time=480))
        midi.tracks.append(track)

        chords = [(0.0, "E")]
        report = lock_midi(midi, "C maior", chords=chords)

        notes = [msg.note for msg in midi.tracks[0] if msg.type == "note_on" and msg.velocity > 0]
        self.assertEqual(notes, [68])

    def test_harmonic_lock_bass_follows_chord_inversion(self):
        """Baixo deve seguir a nota de baixo especificada na inversão (ex: C/G toca Sol)."""
        midi = mido.MidiFile(type=1)
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name="Bass", time=0))
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
        # Nota F# (30) desafinada tocada no momento do acorde C/G (baixo deve ser G = 31 ou 43)
        track.append(mido.Message("note_on", channel=0, note=30, velocity=85, time=0))
        track.append(mido.Message("note_off", channel=0, note=30, velocity=0, time=480))
        midi.tracks.append(track)

        chords = [(0.0, "C/G")]
        report = lock_midi(midi, "C maior", chords=chords)

        notes = [msg.note for msg in midi.tracks[0] if msg.type == "note_on" and msg.velocity > 0]
        # Nota deve ter sido corrigida para G (Sol, pitch class 7 -> MIDI 31)
        self.assertEqual(notes[0] % 12, 7)
        self.assertTrue(24 <= notes[0] <= 58)

    def test_harmonic_lock_leaves_drums_completely_unchanged(self):
        """Canal 10 (canal 9 no mido) ou faixas de percussão não sofrem nenhuma alteração."""
        midi = mido.MidiFile(type=1)
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name="Drums", time=0))
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
        # Bumbo (36) e Caixa (38) no canal 9
        track.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))
        track.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=240))
        track.append(mido.Message("note_on", channel=9, note=38, velocity=95, time=240))
        track.append(mido.Message("note_off", channel=9, note=38, velocity=0, time=480))
        midi.tracks.append(track)

        chords = [(0.0, "F#m")]
        lock_midi(midi, "D maior", chords=chords)

        drum_pitches = [msg.note for msg in midi.tracks[0] if msg.type == "note_on" and msg.velocity > 0]
        self.assertEqual(drum_pitches, [36, 38])

    def test_musical_style_preserves_fast_repeated_notes(self):
        """Notas rápidas consecutivas separadas por mais de 15ms não devem ser fundidas."""
        midi = pretty_midi.PrettyMIDI(initial_tempo=150)
        inst = pretty_midi.Instrument(25, name="Guitar")
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=52, start=0.0, end=0.08))
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=52, start=0.10, end=0.18))
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=52, start=0.20, end=0.28))
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=52, start=0.30, end=0.38))
        midi.instruments.append(inst)

        styled = apply_style(midi, "guitar", bpm=150, grid_times=np.array([]), grid=0)
        self.assertEqual(len(styled.instruments[0].notes), 4)

    def test_drum_timing_locks_to_musical_grid(self):
        """A bateria trava com precisão no tempo certo da música, eliminando oscilações indesejadas."""
        midi = pretty_midi.PrettyMIDI(initial_tempo=120)
        inst = pretty_midi.Instrument(0, is_drum=True, name="Drums")
        # Ataque de caixa aos 0.145s (com leve variação humana/acústica em relação a 0.125s)
        inst.notes.append(pretty_midi.Note(velocity=90, pitch=38, start=0.145, end=0.25))
        # Ataque de chimbal aos 0.148s que deve alinhar no mesmo tempo da caixa
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=42, start=0.148, end=0.22))
        midi.instruments.append(inst)

        grid_times = np.arange(0.0, 2.0, 0.125)  # semicolcheias em 120 bpm (0.125s)
        quantized = quantize_drums(midi, bpm=120, grid_times=grid_times, grid=16)
        notes = quantized.instruments[0].notes
        # A caixa foi cravada no tempo certo da grade (0.125s)
        self.assertAlmostEqual(notes[0].start, 0.125, delta=0.001)
        # O chimbal foi alinhado exatamente ao mesmo instante da batida
        self.assertAlmostEqual(notes[1].start, 0.125, delta=0.001)

    def test_bass_groove_sustains_notes(self):
        """O baixo deve sustentar suas notas para garantir o groove, sem cortes prematuros."""
        from bass_pitch import sustain_bass_groove
        midi = pretty_midi.PrettyMIDI(initial_tempo=120)
        bass_inst = pretty_midi.Instrument(33, name="Bass")
        # Duas notas curtas (staccato de 80ms) espaçadas por 0.5s (uma semínima a 120 bpm)
        bass_inst.notes.append(pretty_midi.Note(velocity=90, pitch=36, start=0.0, end=0.08))
        bass_inst.notes.append(pretty_midi.Note(velocity=90, pitch=43, start=0.5, end=0.58))
        midi.instruments.append(bass_inst)

        sustained = sustain_bass_groove(midi, bpm=120)
        notes = sustained.instruments[0].notes
        # A primeira nota deve ter sido estendida até quase o início da segunda nota (0.5s - 0.03s = 0.47s)
        self.assertGreaterEqual(notes[0].end, 0.45)
        # A última nota deve sustentar por pelo menos 0.75s para não morrer no vácuo
        self.assertGreaterEqual(notes[1].end, 1.0)

    def test_harmonic_lock_preserves_melodic_solos_and_corrects_out_of_scale_notes(self):
        """Preserva notas de solos melódicos que pertencem à escala e corrige notas dissonantes fora da escala."""
        midi = mido.MidiFile(type=1)
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name="Piano", time=0))
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
        # Nota 65 (Fá) - nota melódica na escala de C maior (C D E F G A B) durante acorde C maior:
        # DEVE SER PRESERVADA para não destruir o solo!
        track.append(mido.Message("note_on", channel=1, note=65, velocity=80, time=0))
        track.append(mido.Message("note_off", channel=1, note=65, velocity=0, time=240))
        # Nota 66 (F#) - nota fora da escala de C maior e fora do acorde C maior:
        # DEVE SER CORRIGIDA para o acorde (64 ou 67)
        track.append(mido.Message("note_on", channel=1, note=66, velocity=80, time=240))
        track.append(mido.Message("note_off", channel=1, note=66, velocity=0, time=480))
        midi.tracks.append(track)

        chords = [(0.0, "C")]
        lock_midi(midi, "C maior", chords=chords)

        notes = [msg.note for msg in midi.tracks[0] if msg.type == "note_on" and msg.velocity > 0]
        # Nota 65 (Fá) permaneceu 65 (solo preservado)
        self.assertEqual(notes[0], 65)
        # Nota 66 (F#) foi corrigida para nota do acorde (64=Mi ou 67=Sol)
        self.assertIn(notes[1], (64, 67))

    def test_keyboard_enhancer_marks_time_and_provides_pad(self):
        """Keyboard enhancer gera marcação rítmica no piano e sustentação afinada no other."""
        from keyboard_enhancer import enhance_keyboards
        with tempfile.TemporaryDirectory() as tmpdir:
            midi_dir = Path(tmpdir)
            # Cria piano inicial com 8 batidas
            p_midi = pretty_midi.PrettyMIDI(initial_tempo=120)
            p_inst = pretty_midi.Instrument(0, name="Piano")
            for t in np.arange(0.0, 4.0, 0.5):
                p_inst.notes.append(pretty_midi.Note(velocity=70, pitch=60, start=t, end=t + 0.3))
            p_midi.instruments.append(p_inst)
            p_midi.write(str(midi_dir / "piano.mid"))

            # Cria other com notas
            o_midi = pretty_midi.PrettyMIDI(initial_tempo=120)
            o_inst = pretty_midi.Instrument(0, name="Other - Acompanhamento nao identificado")
            o_inst.notes.append(pretty_midi.Note(velocity=65, pitch=64, start=0.0, end=1.8))
            o_midi.instruments.append(o_inst)
            o_midi.write(str(midi_dir / "other.mid"))

            chords = [(0.0, "Am"), (2.0, "F")]
            res = enhance_keyboards(midi_dir, bpm=120.0, chords=chords, duration=4.0, key="Am")

            self.assertEqual(res["status"], "pianos_harmonized_and_marked")
            self.assertGreater(res["piano_notes"], 0)
            self.assertGreater(res["other_notes"], 0)

            # Confere que as notas do piano respeitam a harmonia dos acordes Am e F
            enhanced_piano = pretty_midi.PrettyMIDI(str(midi_dir / "piano.mid"))
            am_pcs = {9, 0, 4} # A, C, E
            f_pcs = {5, 9, 0}  # F, A, C
            for n in enhanced_piano.instruments[0].notes:
                if n.start < 2.0:
                    self.assertIn(n.pitch % 12, am_pcs)
                else:
                    self.assertIn(n.pitch % 12, f_pcs)

    def test_drum_classifier_allows_clean_toms(self):
        """O classificador de corpo não descarta tons acústicos com baixo ruído."""
        from drum_classifier import classify_body
        sr = 44100
        # Onda senoidal de 130 Hz (tom acústico) com decaimento exponencial
        t = np.linspace(0, 0.25, int(sr * 0.25))
        y_tom = np.sin(2 * np.pi * 130 * t) * np.exp(-t * 12)
        tom_note, sub_frac, has_snare = classify_body(y_tom, sr, onset=0.0)
        # Deve classificar como tom (uma das notas de TOM_NOTES) e não como esteira de caixa (snare)
        self.assertIn(tom_note, (41, 43, 45, 47, 48, 50))
        self.assertFalse(has_snare)

    def test_drum_classifier_floor_tom_detection(self):
        """Surdos graves (Floor Tom entre 58 e 90 Hz) devem ser classificados como MIDI 41."""
        from drum_classifier import classify_body
        sr = 44100
        t = np.linspace(0, 0.25, int(sr * 0.25))
        # Onda senoidal pura de 72 Hz (surdo grave de 16") com cauda ressonante
        y_floor_tom = np.sin(2 * np.pi * 72 * t) * np.exp(-t * 9)
        tom_note, sub_frac, has_snare = classify_body(y_floor_tom, sr, onset=0.0)
        self.assertEqual(tom_note, 41)
        self.assertFalse(has_snare)

    def test_transcribe_drums_cymbals_and_toms(self):
        """Transcribe drums deve detectar pratos (Crash/Ride) e tons/surdo simultâneos com fidelidade."""
        from audio2midi_core import transcribe_drums
        sr = 22050
        duration = 3.5
        audio = np.zeros(int(sr * duration), dtype=np.float32)

        # 0.0s: Bumbo (50 Hz) + Prato Crash (ruído metálico agudo sustentado)
        t_kick = np.linspace(0, 0.2, int(sr * 0.2))
        audio[:len(t_kick)] += np.sin(2 * np.pi * 50 * t_kick) * np.exp(-t_kick * 20)
        t_crash = np.linspace(0, 1.0, int(sr * 1.0))
        audio[:len(t_crash)] += np.random.uniform(-1, 1, len(t_crash)) * np.exp(-t_crash * 3.5) * 0.7

        # 1.0s: Caixa (180 Hz com esteira)
        idx_caixa = int(1.0 * sr)
        t_caixa = np.linspace(0, 0.2, int(sr * 0.2))
        audio[idx_caixa:idx_caixa+len(t_caixa)] += (0.5 * np.sin(2 * np.pi * 180 * t_caixa) + 0.6 * np.random.uniform(-1, 1, len(t_caixa))) * np.exp(-t_caixa * 18)

        # 2.0s: Surdo grave acústico (75 Hz)
        idx_surdo = int(2.0 * sr)
        t_surdo = np.linspace(0, 0.3, int(sr * 0.3))
        audio[idx_surdo:idx_surdo+len(t_surdo)] += np.sin(2 * np.pi * 75 * t_surdo) * np.exp(-t_surdo * 10) * 0.8

        # 2.5s: Tom acústico agudo (220 Hz)
        idx_tom = int(2.5 * sr)
        t_tom = np.linspace(0, 0.25, int(sr * 0.25))
        audio[idx_tom:idx_tom+len(t_tom)] += np.sin(2 * np.pi * 220 * t_tom) * np.exp(-t_tom * 12) * 0.8

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "drums.wav"
            sf.write(str(wav_path), audio, sr)
            midi = transcribe_drums(wav_path, bpm=120.0, grid_times=np.array([]))
            notes = [(round(n.start, 2), n.pitch) for inst in midi.instruments for n in inst.notes]
            pitches = [p for _, p in notes]

            # Deve conter Bumbo (36), Caixa (38), Prato Crash (49), Surdo (41) e Tom (47 ou 48)
            self.assertIn(36, pitches)
            self.assertIn(38, pitches)
            self.assertIn(49, pitches)
            self.assertIn(41, pitches)
            self.assertTrue(any(p in (47, 48, 50) for p in pitches))

    def test_normalize_youtube_url(self):
        """Aceita links complexos do YouTube com parâmetros de rádio, playlist e canais."""
        from pipeline import normalize_youtube_url
        url_radio = "https://www.youtube.com/watch?v=a0v4ucMOfj4&list=RDa0v4ucMOfj4&start_radio=1"
        self.assertEqual(normalize_youtube_url(url_radio), "https://www.youtube.com/watch?v=a0v4ucMOfj4")

        url_short = "https://youtu.be/a0v4ucMOfj4?t=15"
        self.assertEqual(normalize_youtube_url(url_short), "https://www.youtube.com/watch?v=a0v4ucMOfj4")

        url_shorts = "https://www.youtube.com/shorts/a0v4ucMOfj4"
        self.assertEqual(normalize_youtube_url(url_shorts), "https://www.youtube.com/watch?v=a0v4ucMOfj4")

    def test_karaoke_sync_aligns_without_clustering(self):
        """Distribui as palavras da letra compasso a compasso sem amontoar no mesmo segundo."""
        from karaoke_sync import align_karaoke_lyrics_rhythmically, is_karaoke_source
        self.assertTrue(is_karaoke_source("Karaokê - Mega Hair - Netto Brito, Luan Pereira"))

        lyrics = (
            "Hoje eu acordei lembrando de você\n"
            "Com uma saudade doida no meu peito\n\n"
            "Pega o mega hair e joga no meu colo\n"
            "Que hoje a noite promete"
        )
        duration = 60.0
        bpm = 138.0
        words = align_karaoke_lyrics_rhythmically(lyrics, duration=duration, bpm=bpm, intro_seconds=8.0)

        self.assertGreater(len(words), 10)
        # O canto deve começar após a introdução (8.0s)
        self.assertGreaterEqual(words[0]["start"], 7.9)

        # Nenhuma palavra deve durar menos de 80ms nem começar no mesmo instante da anterior
        for i in range(len(words) - 1):
            curr_w = words[i]
            next_w = words[i + 1]
            self.assertGreaterEqual(curr_w["end"], curr_w["start"] + 0.08)
            # O início da próxima palavra deve ser maior que o início da atual (sem amontoamento)
            self.assertGreater(next_w["start"], curr_w["start"])

        # O final da última palavra não deve ultrapassar a duração da música
        self.assertLessEqual(words[-1]["end"], duration)

    def test_auto_mix_attenuates_secondary_instruments(self):
        """Instrumentos secundários (como piano2/other) devem ter volume atenuado em relação ao lead."""
        from auto_mix import calculate_relative_volumes, apply_auto_mix
        sr = 22050
        duration = 3.0
        # Piano 1 com volume alto (lead)
        t = np.linspace(0, duration, int(sr * duration))
        y_piano1 = np.sin(2 * np.pi * 440 * t) * 0.8
        # Piano 2 com volume mais baixo (accompaniment de fundo)
        y_piano2 = np.sin(2 * np.pi * 440 * t) * 0.25

        with tempfile.TemporaryDirectory() as tmpdir:
            stems_dir = Path(tmpdir)
            sf.write(str(stems_dir / "piano.wav"), y_piano1, sr)
            sf.write(str(stems_dir / "other.wav"), y_piano2, sr)

            volumes = calculate_relative_volumes(stems_dir)
            self.assertIn("piano", volumes)
            self.assertIn("other", volumes)
            # Piano 2 (other) deve ter volume menor que Piano 1
            self.assertLess(volumes["other"], volumes["piano"])

            # Teste de aplicação no MIDI
            midi = mido.MidiFile(type=1)
            p_track = mido.MidiTrack()
            p_track.append(mido.MetaMessage("track_name", name="Piano", time=0))
            p_track.append(mido.Message("note_on", channel=5, note=60, velocity=100, time=0))
            p_track.append(mido.Message("note_off", channel=5, note=60, velocity=0, time=480))
            midi.tracks.append(p_track)

            o_track = mido.MidiTrack()
            o_track.append(mido.MetaMessage("track_name", name="Other", time=0))
            o_track.append(mido.Message("note_on", channel=6, note=64, velocity=100, time=0))
            o_track.append(mido.Message("note_off", channel=6, note=64, velocity=0, time=480))
            midi.tracks.append(o_track)

            mixed_midi = apply_auto_mix(midi, volumes)
            # Verifica que foram inseridas mensagens de CC 7 (control_change volume)
            cc7_p = [m.value for m in mixed_midi.tracks[0] if m.type == "control_change" and m.control == 7]
            cc7_o = [m.value for m in mixed_midi.tracks[1] if m.type == "control_change" and m.control == 7]
            self.assertTrue(len(cc7_p) > 0)
            self.assertTrue(len(cc7_o) > 0)
            self.assertGreater(cc7_p[0], cc7_o[0])

            # E a velocidade da nota do Other foi atenuada
            o_vels = [m.velocity for m in mixed_midi.tracks[1] if m.type == "note_on" and m.velocity > 0]
            self.assertLess(o_vels[0], 100)

    def test_auto_mix_lower_second_piano_explicit_option(self):
        """A opção de abaixar o segundo piano reduz o volume do acompanhamento para segundo plano."""
        from auto_mix import calculate_relative_volumes
        sr = 22050
        duration = 2.0
        t = np.linspace(0, duration, int(sr * duration))
        y = np.sin(2 * np.pi * 440 * t) * 0.6

        with tempfile.TemporaryDirectory() as tmpdir:
            stems_dir = Path(tmpdir)
            sf.write(str(stems_dir / "piano.wav"), y, sr)
            sf.write(str(stems_dir / "other.wav"), y, sr)

            # Com lower_second_piano=True
            vol_low = calculate_relative_volumes(stems_dir, lower_second_piano=True)
            # Com lower_second_piano=False
            vol_normal = calculate_relative_volumes(stems_dir, lower_second_piano=False)

            self.assertLess(vol_low["other"], vol_normal["other"])
            self.assertLessEqual(vol_low["other"], 75)

    def test_export_karaoke_synchronizes_lyrics_in_mid_and_kar(self):
        """export_karaoke gera tanto karaoke.mid quanto karaoke.kar com eventos sincronizados."""
        from lyrics import export_karaoke
        base_midi = mido.MidiFile(type=1)
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
        track.append(mido.Message("note_on", channel=0, note=60, velocity=80, time=0))
        track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=480))
        base_midi.tracks.append(track)

        events = [
            {"seconds": 0.5, "text": "Hoje "},
            {"seconds": 1.0, "text": "eu "},
            {"seconds": 1.5, "text": "acordei"},
            {"seconds": 2.0, "text": "[QUEBRA]"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            res = export_karaoke(base_midi, events, out_dir)
            self.assertEqual(len(res), 4)

            # Verifica se gerou tanto o .mid quanto o .kar
            mid_path = out_dir / "karaoke.mid"
            kar_path = out_dir / "karaoke.kar"
            self.assertTrue(mid_path.is_file())
            self.assertTrue(kar_path.is_file())

            # Lê o karaoke.mid e garante que a trilha de lyrics está presente
            mid = mido.MidiFile(mid_path, charset="latin1")
            lyric_msgs = [m.text for t in mid.tracks for m in t if m.type == "lyrics"]
            self.assertIn("Hoje ", lyric_msgs)
            self.assertIn("eu ", lyric_msgs)
            self.assertIn("acordei", lyric_msgs)


if __name__ == "__main__":
    unittest.main()
