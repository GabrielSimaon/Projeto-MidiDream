import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from output_settings import directory_choices, directory_roots, configure_resources, save_destination, resource_directory


class DirectoryBrowserTests(unittest.TestCase):
    def test_lists_only_subdirectories_and_sorted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'Zulu').mkdir()
            (root / 'Áudios').mkdir()
            (root / 'alpha').mkdir()
            (root / 'file.mp3').touch()
            self.assertEqual([p.name for p in directory_choices(root)], ['alpha', 'Zulu', 'Áudios'])
            self.assertTrue(all(p.is_dir() for p in directory_roots()))

    def test_resource_and_destination_settings_survive_updates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / 'settings.json'
            with patch.dict(os.environ), patch.object(tempfile, 'tempdir', tempfile.tempdir):
                resources = configure_resources(config, str(root / 'recursos'))
                save_destination(config, str(root / 'musicas'))
                self.assertEqual(resource_directory(config, 'unused'), str(resources))
                self.assertEqual(Path(os.environ['HF_HOME']), resources / 'huggingface')
                self.assertEqual(Path(os.environ['TEMP']), resources / 'temporarios')
                self.assertTrue(Path(tempfile.gettempdir()).is_dir())
                self.assertIn('destination', json.loads(config.read_text()))

    def test_weight_download_checks_disk_before_writing(self):
        from instrument_worker import download_weights
        from types import SimpleNamespace
        response = SimpleNamespace(status=200, headers={'Content-Length': str(1024**3)})
        with tempfile.TemporaryDirectory() as temporary:
            with patch('urllib.request.urlopen') as request, patch('instrument_worker.shutil.disk_usage', return_value=SimpleNamespace(free=70*1024**2)):
                request.return_value.__enter__.return_value = response
                with self.assertRaisesRegex(OSError, 'Espaço insuficiente'):
                    download_weights(Path(temporary))
            self.assertFalse(list(Path(temporary).rglob('*.part')))

    def test_unreadable_f_drive_does_not_hide_e_drive(self):
        from types import SimpleNamespace
        def is_dir(path):
            if str(path).startswith('F:'):
                raise OSError(1005, 'O volume não contém um sistema de arquivos reconhecido')
            return str(path).startswith('E:')
        with patch('output_settings.os', SimpleNamespace(name='nt')), patch.object(Path, 'is_dir', is_dir):
            self.assertEqual(directory_roots(), [Path('E:/')])

    def test_unreadable_child_is_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'musicas').mkdir()
            (root / 'inacessivel').mkdir()
            original = Path.is_dir
            def is_dir(path):
                if path.name == 'inacessivel':
                    raise PermissionError('Acesso negado')
                return original(path)
            with patch.object(Path, 'is_dir', is_dir):
                self.assertEqual(directory_choices(root), [root / 'musicas'])
