"""Pastas persistentes e navegação de diretórios na interface."""
import json
import os
from pathlib import Path
import tempfile
import uuid


def load_destination(config, default):
    try:
        value = json.loads(Path(config).read_text(encoding="utf-8"))["destination"]
        return value if isinstance(value, str) and value.strip() else str(default)
    except (OSError, ValueError, KeyError, TypeError):
        return str(default)


def save_destination(config, destination):
    path = Path(destination.strip()).expanduser()
    if not destination.strip() or not path.is_absolute():
        raise ValueError("Informe o caminho completo da pasta de destino.")
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path):
        pass
    config = Path(config)
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["destination"] = str(path)
    _write_settings(config, data)
    return path


def save_lyric_files(destination, name, data):
    folder = Path(destination) / (Path(name).name + "_" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    for suffix in (".mid", ".kar"):
        (folder / (Path(name).name + suffix)).write_bytes(data)
    return folder


def accessible_directory(path):
    try:
        return Path(path).is_dir()
    except OSError:
        # Unidades sem mídia, desconectadas ou com volume ilegível.
        return False


def directory_choices(directory):
    path = Path(directory).expanduser().resolve()
    return sorted((p for p in path.iterdir() if accessible_directory(p)), key=lambda p: p.name.casefold())


def directory_roots():
    candidates = ([Path(f"{letter}:/") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
                  if os.name == "nt" else [Path("/"), Path.home()])
    return [path for path in candidates if accessible_directory(path)]


def resource_directory(config, default):
    try:
        value = json.loads(Path(config).read_text(encoding="utf-8")).get("resources")
        return str(value or default)
    except (OSError, ValueError, TypeError):
        return str(default)


def configure_resources(config, destination):
    path = Path(destination.strip()).expanduser()
    if not destination.strip() or not path.is_absolute():
        raise ValueError("Informe o caminho completo da pasta dos modelos.")
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path):
        pass
    config = Path(config)
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["resources"] = str(path)
    _write_settings(config, data)
    for key, folder in {"HF_HOME": "huggingface", "HF_HUB_CACHE": "huggingface/hub",
                        "HUGGINGFACE_HUB_CACHE": "huggingface/hub", "TORCH_HOME": "torch",
                        "XDG_CACHE_HOME": "cache", "UV_CACHE_DIR": "uv_cache",
                        "PIP_CACHE_DIR": "pip_cache", "TEMP": "temporarios",
                        "TMP": "temporarios", "TMPDIR": "temporarios"}.items():
        os.environ[key] = str(path / folder)
    temporary_dir = path / "temporarios"
    temporary_dir.mkdir(exist_ok=True)
    tempfile.tempdir = str(temporary_dir)
    return path


def _write_settings(config, data):
    config = Path(config)
    config.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config.parent,
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False)
    try:
        temporary.replace(config)
    finally:
        temporary.unlink(missing_ok=True)
