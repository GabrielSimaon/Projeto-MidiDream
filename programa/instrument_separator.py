"""Prepara o motor opcional sem substituir as dependências do aplicativo."""
import json
import os
from pathlib import Path
import sys
import shutil
from performance import cache_key, read_cache, save_cache

from instrument_detection import TARGETS


def ensure_runtime(root, run, progress=None):
    root = Path(root)
    env = root / "ambiente_instrumentos"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = env / "pronto-1.json"
    if marker.is_file() and python.is_file():
        return python
    if progress:
        progress("modelo", "Preparando reconhecimento e separação por instrumento (primeiro uso)…")
    manager = os.environ.get("MIDI_STUDIO_UV")
    if manager and Path(manager).is_file():
        if not python.is_file():
            run([manager, "venv", "--system-site-packages", "--python", sys.executable, str(env)])
        installer = [manager, "pip", "install", "--python", str(python)]
    else:
        if not python.is_file():
            run([sys.executable, "-m", "venv", "--system-site-packages", str(env)])
        installer = [str(python), "-m", "pip", "install"]
    run(installer + ["torch==2.5.1", "torchaudio==2.5.1", "torchvision==0.20.1", "--index-url", "https://download.pytorch.org/whl/cpu"])
    run(installer + ["transformers==4.38.2", "huggingface-hub==0.25.2", "pytorch-lightning==2.0.9", "torchmetrics==0.11.4",
        "torchlibrosa==0.1.0", "timm==0.6.13", "ftfy>=6,<7", "webdataset==0.2.57",
        "h5py>=3,<4", "pandas>=1.5,<3", "braceexpand==0.1.7", "wget==3.2", "numpy<2", "setuptools<81", "pyarrow>=14,<20", "scikit-learn>=1.3,<1.6"])
    run([str(python), str(Path(__file__).with_name("instrument_worker.py")), "--check"])
    marker.write_text(json.dumps({"version": 1}), encoding="utf-8")
    return python


def separate_instruments(stems_dir, output_dir, model_root, run, progress=None, *, verify_known=False):
    source, output = Path(stems_dir), Path(output_dir)
    if not source.is_dir():
        raise ValueError("A separação completa exige primeiro separar voz, baixo e bateria.")
    output.mkdir(parents=True, exist_ok=True)
    audio_files = [file for file in source.iterdir() if file.is_file() and file.suffix.lower() in
                   (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".webm")]
    key = cache_key([(file.name, file) for file in audio_files], {"version": 4, "presence_threshold": 0.20, "verify_known": verify_known, "fallback": True})
    cache_root = Path(model_root) / "cache_instrumentos"
    cached = read_cache(cache_root, key, ["instrumentos.json"])
    if cached:
        for file in cached.iterdir():
            if file.name == "complete.json":
                continue
            destination = output.parent / file.name if file.name == "acompanhamento_nao_identificado.wav" else output / file.name
            shutil.copy2(file, destination)
        if progress:
            progress("instrumentos", "Separação por instrumento recuperada do cache.")
        return output
    python = ensure_runtime(model_root, run, progress)
    command = [str(python), str(Path(__file__).with_name("instrument_worker.py")),
               "--source", str(source.resolve()), "--output", str(output.resolve()),
               "--models", str(Path(model_root).resolve())]
    if verify_known:
        command.append("--verify-known")
    def report(line):
        if progress and line.startswith("INSTRUMENT "):
            progress("instrumentos", line.removeprefix("INSTRUMENT "))
    run(command, on_output=report)
    manifest = output / "instrumentos.json"
    if not manifest.is_file():
        raise RuntimeError("O motor não concluiu a separação por instrumento.")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for kind in data["exported"]:
        if kind not in {*TARGETS, "guitar", "other"} or not (output / (kind + ".wav")).is_file():
            raise RuntimeError("O motor retornou uma faixa inválida.")
    try:
        from bleed_cleaner import normalize_audio_stems
        normalize_audio_stems(output)
    except Exception as exc:
        print(f"[aviso] Normalização em separate_instruments: {exc}")
    if not data.get("errors"):
        save_cache(cache_root, key, [file for file in output.iterdir() if file.is_file()] +
                   [output.parent / "acompanhamento_nao_identificado.wav"])
    return output
