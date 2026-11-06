"""Renderiza os MIDIs com o banco de samples incluído, sem alterar as notas."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

BASE = Path(__file__).resolve().parent
SOUNDFONT = BASE / "samples" / "GeneralUser-GS.sf2"


def synthesizer():
    if os.name == "nt":
        binaries = sorted((BASE / "ferramentas" / "fluidsynth").rglob("fluidsynth.exe"))
        if binaries:
            return str(binaries[0])
    executable = shutil.which("fluidsynth")
    if executable:
        return executable
    raise RuntimeError("FluidSynth não encontrado. Extraia o ZIP completo, incluindo ferramentas e samples.")


def render_midi(source, target, soundfont=SOUNDFONT):
    source, target, soundfont = Path(source), Path(target), Path(soundfont)
    if not soundfont.is_file() or soundfont.stat().st_size < 1024:
        raise FileNotFoundError("Banco de samples ausente: " + str(soundfont))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="render_", dir=target.parent) as folder:
        wav = Path(folder) / "audio.wav"
        cmd = [synthesizer(), "-ni", "-q", "-g", "0.2", "-r", "44100",
               "-T", "wav", "-O", "s16", "-F", str(wav), str(soundfont), str(source)]
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not wav.is_file() or wav.stat().st_size <= 44:
            raise RuntimeError("Falha ao renderizar " + source.name + ": " + result.stderr[-1500:])
        if target.suffix == ".wav":
            wav.replace(target)
        else:
            encoded = Path(folder) / "audio.mp3"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(wav),
                            "-codec:a", "libmp3lame", "-q:a", "2", str(encoded)],
                           capture_output=True, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            encoded.replace(target)
    return target


def render_result(result_dir, progress=None):
    result_dir = Path(result_dir)
    output = result_dir / "audio_samples"
    files = sorted(p for p in (result_dir / "midi").glob("*.mid")
                   if p.stem not in {"karaoke", "com_letra"})
    report = {"bank": "GeneralUser GS 2.0.3", "files": [], "errors": []}
    for index, midi in enumerate(files):
        if progress:
            progress("samples", f"Gerando áudio com samples: {midi.stem} ({index + 1}/{len(files)})")
        target = output / (midi.stem + ".mp3")
        try:
            render_midi(midi, target)
            report["files"].append(target.name)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            # Evita oferecer uma prévia antiga depois de mudar os volumes.
            target.unlink(missing_ok=True)
            report["errors"].append(f"{midi.name}: {exc}")
    (result_dir / "samples.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def start_midi_playback(source, log_path):
    """Toca o MIDI diretamente com o SoundFont, sem converter para MP3."""
    source = Path(source)
    if not source.is_file() or not SOUNDFONT.is_file():
        raise FileNotFoundError("MIDI ou banco de samples não encontrado.")
    command = [synthesizer(), "-ni", "-q", "-g", "0.2", str(SOUNDFONT), str(source)]
    with Path(log_path).open("wb") as log:
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
