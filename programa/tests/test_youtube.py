"""Download testado sem acessar sessões pessoais nem fazer requisições ao YouTube."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import download_youtube, normalize_youtube_url, YouTubeDownloadError


class YouTubeTests(unittest.TestCase):
    def test_normalize_video_without_radio_or_playlist(self):
        canonical = "https://www.youtube.com/watch?v=Y0pdQU87dc8"
        for url in [
            canonical + "&list=RDY0pdQU87dc8&start_radio=1",
            "https://youtu.be/Y0pdQU87dc8?t=15",
            "https://www.youtube.com/shorts/Y0pdQU87dc8",
            "https://www.youtube.com/live/Y0pdQU87dc8",
        ]:
            self.assertEqual(normalize_youtube_url(url), canonical)

    def test_reject_unknown_host_and_playlist(self):
        for url in [
            "https://youtube.com.evil.test/watch?v=Y0pdQU87dc8",
            "https://www.youtube.com/playlist?list=RD1",
            "https://www.youtube.com/watch?v=invalid",
        ]:
            with self.assertRaises(ValueError):
                normalize_youtube_url(url)

    def test_default_does_not_access_cookies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "musica.wav").touch()
            with patch("pipeline._run", return_value="ok") as run:
                result = download_youtube("https://youtu.be/Y0pdQU87dc8", root)
            command = run.call_args.args[0]
            self.assertNotIn("--cookies-from-browser", command)
            self.assertNotIn("--cookies", command)
            self.assertEqual(command[-2:], ["--", "https://www.youtube.com/watch?v=Y0pdQU87dc8"])
            self.assertEqual(result, root / "musica.wav")

    def test_explicit_browser_session_is_not_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "musica.wav").touch()
            with patch("pipeline._run", return_value="ok") as run:
                download_youtube("https://youtu.be/Y0pdQU87dc8", root, browser="firefox")
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--cookies-from-browser") + 1], "firefox")
            self.assertNotIn("--cookies", command)
            self.assertEqual(list(root.iterdir()), [root / "musica.wav"])

    def test_429_does_not_trigger_automatic_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "pipeline._run",
                side_effect=RuntimeError("command\n\nHTTP Error 429: Too Many Requests"),
            ) as run:
                with self.assertRaises(YouTubeDownloadError) as error:
                    download_youtube("https://youtu.be/Y0pdQU87dc8", Path(directory))
            run.assert_called_once()
            self.assertIn("Usar minha sessão", str(error.exception))
            self.assertNotIn("Comando falhou", str(error.exception))

    def test_browser_encryption_error_has_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "pipeline._run", side_effect=RuntimeError("command\n\nFailed to decrypt with DPAPI")
            ):
                with self.assertRaises(YouTubeDownloadError) as error:
                    download_youtube("https://youtu.be/Y0pdQU87dc8", Path(directory), "chrome")
            self.assertIn("Firefox", str(error.exception))

    def test_invalid_browser_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                download_youtube("https://youtu.be/Y0pdQU87dc8", Path(directory), "arbitrary:path")


if __name__ == "__main__":
    unittest.main()
