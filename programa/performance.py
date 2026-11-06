"""Perfis de processamento e cache por conteúdo; não depende do nome do arquivo."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def cache_key(files, settings):
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())
    for name, file in sorted(files):
        digest.update(name.encode())
        with Path(file).open("rb") as audio:
            for block in iter(lambda: audio.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def read_cache(root, key, required):
    folder = Path(root) / key
    try:
        manifest = json.loads((folder / "complete.json").read_text())
        if all(name in manifest for name in required) and all(
            (folder / name).is_file() and (folder / name).stat().st_size == size
            for name, size in manifest.items()
        ):
            return folder
    except (OSError, ValueError, TypeError):
        pass
    return None


def save_cache(root, key, files):
    """Publica apenas um resultado completo, sem aproveitar processamento interrompido."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="pending_", dir=root))
    try:
        manifest = {}
        for file in files:
            file = Path(file)
            shutil.copy2(file, temporary / file.name)
            manifest[file.name] = file.stat().st_size
        (temporary / "complete.json").write_text(json.dumps(manifest))
        target = root / key
        try:
            temporary.rename(target)
        except OSError:
            # Outra execução pode ter publicado o mesmo conteúdo primeiro.
            if not target.exists():
                raise
            if read_cache(root, key, manifest) is None:
                shutil.rmtree(target)
                temporary.rename(target)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def cpu_threads():
    """Usa até quatro núcleos físicos no Windows, sem confundir Hyper-Threading."""
    import os

    if os.name == "nt":
        try:
            import ctypes
            import struct

            size = ctypes.c_ulong()
            query = ctypes.WinDLL("kernel32").GetLogicalProcessorInformationEx
            query.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            query.restype = ctypes.c_int
            query(0, None, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if query(0, buffer, ctypes.byref(size)):
                offset = cores = 0
                while offset < size.value:
                    relationship, length = struct.unpack_from("II", buffer, offset)
                    if not length:
                        break
                    cores += relationship == 0
                    offset += length
                if cores:
                    return min(4, cores)
        except (OSError, ValueError, AttributeError):
            pass
    return max(1, min(4, os.cpu_count() or 1))
