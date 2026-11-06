"""Localiza os componentes do download, inclusive fora do PATH do Windows."""

import os
import shutil
from pathlib import Path


def download_tool_args():
    args = []
    directories = []
    if os.environ.get("MIDI_STUDIO_TOOLS"):
        directories.append(Path(os.environ["MIDI_STUDIO_TOOLS"]))
    directories.append(Path(__file__).resolve().parent / "ferramentas")
    for name in ("deno", "node"):
        executable = next((str(root / (name + ".exe")) for root in directories
                           if (root / (name + ".exe")).is_file()), None) or shutil.which(name)
        if executable:
            args.extend(["--js-runtimes", name + ":" + executable])
            break
    ffmpeg = next((str(root) for root in directories if (root / "ffmpeg.exe").is_file()), None)
    if ffmpeg:
        args.extend(["--ffmpeg-location", ffmpeg])
    return args


def recoverable_download_error(details):
    low = details.casefold()
    if any(term in low for term in ("429", "not a bot", "sign in", "confirm you", "dpapi", "decrypt", "cookie database")):
        return False
    return any(term in low for term in (
        "403", "signature", "nsig", "javascript", "challenge", "requested format",
        "no module named yt_dlp", "no such option", "ejs",
    ))
