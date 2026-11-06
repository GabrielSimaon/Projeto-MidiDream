import sys, tempfile, json, shutil, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_paths import repair_paths, find_moved_input, find_moved_job
from song_names import rename_song_result
from saved_results import save_result_state, recover_result
from test_studio import fixture


class PathRecoveryTests(unittest.TestCase):
    def prepare(self, root):
        job = root / "job_original"
        (job / "entrada").mkdir(parents=True)
        (job / "resultados/midi").mkdir(parents=True)
        filename = "Felipe & Rodrigo - Pós (Ao Vivo) #NoSentimento [7qcq8wAX9pU].mp3"
        source = job / "entrada" / filename
        source.write_bytes(b"audio")
        fixture(job / "resultados/midi/completo.mid")
        result = {"job_dir": job, "source_audio": source, "stems_dir": job / "stems_separados"}
        save_result_state(result)
        return job, source

    def test_windows_absolute_paths_rebased_without_windows_runtime(self):
        data = {
            "job_dir": r"C:\Users\PC\trabalhos\job_old",
            "source_audio": r"C:\Users\PC\trabalhos\job_old\entrada\Pós [video].mp3",
        }
        with tempfile.TemporaryDirectory() as folder:
            repaired = repair_paths(data, Path(folder))
            self.assertEqual(
                Path(repaired["source_audio"]), Path(folder) / "entrada/Pós [video].mp3"
            )

    def test_manual_folder_move_repairs_snapshot_and_bracketed_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job, source = self.prepare(root)
            target = root / "Minha música"
            job.rename(target)
            self.assertEqual(find_moved_input(source), target / "entrada" / source.name)
            self.assertEqual(find_moved_job(job), target)
            result = recover_result(target)
            self.assertTrue(Path(result["source_audio"]).is_file())
            self.assertEqual(Path(result["source_audio"]), target / "entrada" / source.name)
            self.assertTrue(Path(result["combined_mid"]).is_file())

    def test_renamed_job_is_located_by_alias_and_all_internal_paths_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job, source = self.prepare(root)
            result = rename_song_result(recover_result(job), "Música nomeada")
            self.assertEqual(find_moved_job(job), Path(result["job_dir"]))
            result = recover_result(job)
            for field in ("source_audio", "combined_mid", "archive", "events_path"):
                self.assertTrue(Path(result[field]).is_file(), field)
            saved = json.loads((Path(result["job_dir"]) / "resultado_salvo.json").read_text())
            self.assertEqual(saved["_relative_paths"]["source_audio"], "entrada/" + source.name)

    def test_ambiguous_missing_audio_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job, source = self.prepare(root)
            a = root / "A"
            b = root / "B"
            shutil.copytree(job, a)
            job.rename(b)
            with self.assertRaisesRegex(FileNotFoundError, "mais de uma"):
                find_moved_input(source)

    def test_deleted_audio_does_not_prevent_midi_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            job, source = self.prepare(Path(folder))
            source.unlink()
            result = recover_result(job)
            self.assertTrue(Path(result["combined_mid"]).is_file())
            self.assertTrue(Path(result["archive"]).is_file())
