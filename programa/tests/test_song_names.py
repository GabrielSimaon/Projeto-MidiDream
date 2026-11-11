import sys, json, tempfile, unittest, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from song_names import safe_song_name, song_title, rename_song_result
from saved_results import list_saved_jobs, recover_result
from test_studio import fixture


class NamesTests(unittest.TestCase):
    def test_windows_names(self):
        self.assertEqual(safe_song_name("A/B: C?"), "A B C")
        self.assertEqual(safe_song_name("CON"), "_CON")
        self.assertEqual(safe_song_name("NUL.txt"), "_NUL.txt")
        self.assertLessEqual(len(safe_song_name("a" * 200)), 85)
        self.assertNotIn("/", safe_song_name("../../test"))

    def test_rename_recovery_and_collisions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index in range(2):
                job = root / f"job_{index}"
                (job / "resultados/midi").mkdir(parents=True)
                (job / "entrada").mkdir()
                (job / "entrada/song.mp3").write_bytes(b"dummy")
                fixture(job / "resultados/midi/completo.mid")
                os.utime(job / "resultados/midi/completo.mid", (1700000000, 1700000000))
                result = rename_song_result(recover_result(job), "Minha música")
                path = Path(result["job_dir"])
                self.assertFalse(job.exists())
                self.assertTrue(path.name.startswith("Minha música — "))
                self.assertTrue(Path(result["source_audio"]).exists())
                reopened = recover_result(path)
                self.assertEqual(song_title(path), "Minha música")
                self.assertTrue(Path(reopened["combined_mid"]).exists())
                self.assertEqual(
                    json.loads((path / "resultado_salvo.json").read_text(encoding="utf-8"))["song_title"],
                    "Minha música",
                )
            self.assertEqual(len(list_saved_jobs(root)), 2)

    def test_title_from_existing_input(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            (job / "entrada").mkdir()
            (job / "entrada/Minha Banda.mp3").touch()
            self.assertEqual(song_title(job), "Minha Banda")

    def test_pipeline_uses_youtube_title_and_returns_updated_paths(self):
        import numpy as np
        import soundfile as sf
        from unittest.mock import patch
        from pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder) / "job_yt"

            def download(url, input_dir, browser=None):
                source = input_dir / "musica.wav"
                sf.write(source, np.zeros(16000), 16000)
                (input_dir / "musica.info.json").write_text(
                    json.dumps({"title": "Banda - Ao Vivo"})
                )
                return source

            def engine(cmd, **kwargs):
                dest = Path(cmd[cmd.index("-o") + 1]) / "midi"
                dest.mkdir(parents=True)
                fixture(dest / "completo.mid")
                return "ok"

            with (
                patch("pipeline.download_youtube", side_effect=download),
                patch("pipeline._run", side_effect=engine),
            ):
                result = run_pipeline(
                    job,
                    youtube_url="https://youtube.com/watch?v=Y0pdQU87dc8",
                    separate=False,
                    recognize_lyrics=False,
                    organize_folder=True,
                )
            self.assertFalse(job.exists())
            self.assertEqual(result["song_title"], "Banda - Ao Vivo")
            self.assertTrue(Path(result["combined_mid"]).is_file())
            self.assertTrue(Path(result["archive"]).is_file())
            self.assertTrue(Path(recover_result(result["job_dir"])["source_audio"]).is_file())
