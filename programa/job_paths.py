"""Referências relativas permitem recuperar conversões depois de mover/renomear pastas."""

import json
from pathlib import Path, PureWindowsPath

PATH_FIELDS = (
    "job_dir",
    "source_audio",
    "stems_dir",
    "vocals_path",
    "result_dir",
    "midi_dir",
    "combined_mid",
    "karaoke_mid",
    "karaoke_kar",
    "events_path",
    "lyrics_txt",
    "archive",
)


def portable_path(value):
    value = str(value)
    return (
        PureWindowsPath(value)
        if "\\" in value or (len(value) > 1 and value[1] == ":")
        else Path(value)
    )


def relative_parts(value, root):
    try:
        parts = portable_path(value).relative_to(portable_path(root)).parts
        return parts if ".." not in parts else None
    except (ValueError, TypeError):
        return None


def path_hints(data):
    root = data.get("job_dir")
    if not root:
        return {}
    hints = {}
    for key in PATH_FIELDS:
        if key == "job_dir" or not data.get(key):
            continue
        parts = relative_parts(data[key], root)
        if parts is not None:
            hints[key] = "/".join(parts)
    return hints


def repair_paths(data, job):
    """Prefere arquivos do diretório atual; não depende do caminho absoluto salvo."""
    result, job = dict(data), Path(job).resolve()
    previous = data.get("job_dir")
    hints = data.get("_relative_paths") or {}
    for key in PATH_FIELDS:
        if key == "job_dir" or not data.get(key):
            continue
        parts = None
        if key in hints:
            candidate = Path(hints[key])
            if not candidate.is_absolute() and ".." not in candidate.parts:
                parts = candidate.parts
        if parts is None and previous:
            parts = relative_parts(data[key], previous)
        if parts is not None:
            result[key] = str(job.joinpath(*parts))
    result["job_dir"] = str(job)
    return result


def find_moved_input(source):
    """Procura somente em conversões irmãs e exige um único arquivo correspondente."""
    source = Path(source).expanduser()
    if source.is_file() or source.parent.name != "entrada":
        return source
    old_job = source.parent.parent
    root = old_job.parent
    if not root.is_dir():
        return source
    candidates = [
        job / "entrada" / source.name
        for job in root.iterdir()
        if job.is_dir() and (job / "entrada" / source.name).is_file()
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise FileNotFoundError(
            "Encontrei mais de uma cópia desse áudio. Selecione a conversão no histórico ou envie o MP3 novamente."
        )
    return source


def find_moved_job(job, source_hint=None):
    job = Path(job)
    if job.is_dir() or not job.parent.is_dir():
        return job
    matches = []
    for candidate in job.parent.iterdir():
        if not (candidate / "resultados/midi/completo.mid").is_file():
            continue
        try:
            state = json.loads((candidate / "resultado_salvo.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        old = portable_path(state.get("job_dir", "")).name
        aliases = state.get("previous_job_names", [])
        if old == job.name or job.name in aliases:
            matches.append(candidate)
    if not matches and source_hint:
        name = portable_path(source_hint).name
        matches = [
            p
            for p in job.parent.iterdir()
            if (p / "entrada" / name).is_file() and (p / "resultados/midi/completo.mid").is_file()
        ]
    return matches[0] if len(matches) == 1 else job
