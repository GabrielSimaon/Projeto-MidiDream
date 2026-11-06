"""Nomes legíveis de músicas, sem caracteres inválidos para pastas do Windows."""

import json
import re
from datetime import datetime
from pathlib import Path


def safe_song_name(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(title))
    name = re.sub(r"\s+", " ", name).strip(" .")[:85].rstrip(" .") or "Música sem título"
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = "_" + name
    return name


def song_title(job):
    job = Path(job)
    for filename in ["resultado_salvo.json", "musica.json"]:
        try:
            title = json.loads((job / filename).read_text(encoding="utf-8")).get("song_title")
            if title:
                return str(title)
        except (OSError, ValueError, AttributeError):
            pass
    for file in sorted((job / "entrada").glob("*")):
        if (
            file.suffix.lower() in {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
            and file.stem.lower() not in {"musica", "vocals", "bass", "drums", "other"}
            and not re.fullmatch(r"(vocals|bass|drums|piano|guitar|other)_\d+", file.stem)
        ):
            return file.stem
    return job.name


def rename_song_result(result, title):
    """Move uma conversão concluída e atualiza os caminhos de seu estado salvo."""
    from saved_results import save_result_state

    original = Path(result["job_dir"]).resolve()
    stamp = datetime.fromtimestamp(
        (original / "resultados/midi/completo.mid").stat().st_mtime
    ).strftime("%Y-%m-%d_%Hh%Mm%Ss")
    base = safe_song_name(title) + " — " + stamp
    target = original.parent / base
    suffix = 2
    while target.exists() and target != original:
        target = original.parent / (base + f" ({suffix})")
        suffix += 1
    updated = dict(result)
    for key, value in result.items():
        if isinstance(value, (str, Path)) and (
            key.endswith(("_dir", "_path", "_mid", "_kar", "_txt"))
            or key in ("source_audio", "archive")
        ):
            try:
                relative = Path(value).relative_to(original)
            except ValueError:
                continue
            updated[key] = target / relative
    updated["previous_job_names"] = list(
        dict.fromkeys([*result.get("previous_job_names", []), original.name])
    )
    updated["job_dir"] = target
    updated["song_title"] = title.strip()
    if target != original:
        original.rename(target)
    try:
        save_result_state(updated, status=result.get("saved_status", "completed"))
        (target / "musica.json").write_text(
            json.dumps({"song_title": title.strip()}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        if target != original:
            target.rename(original)
            save_result_state(result, status=result.get("saved_status", "completed"))
        raise
    return updated
