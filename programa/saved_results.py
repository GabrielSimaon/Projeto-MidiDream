"""Resultados em disco independentes da sessão do navegador ou da etapa de letra."""

import json
from pathlib import Path
import subprocess
import zipfile
from job_paths import path_hints, repair_paths, find_moved_job

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".webm"}


def save_result_state(result, status="completed"):
    job = Path(result["job_dir"])
    data = {key: str(value) if isinstance(value, Path) else value for key, value in result.items()}
    data.pop("core_log", None)
    data["saved_status"] = status
    data["_relative_paths"] = path_hints(data)
    temporary = job / "resultado_salvo.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(job / "resultado_salvo.json")


def list_saved_jobs(work_dir, limit=30):
    files = Path(work_dir).glob("*/resultados/midi/completo.mid")
    return [
        p.parents[2] for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    ]


def recover_result(job):
    """Também lê conversões antigas, anteriores à criação do arquivo de estado."""
    import mido

    job = find_moved_job(job).resolve()
    result_dir = job / "resultados"
    midi_dir = result_dir / "midi"
    combined = midi_dir / "completo.mid"
    midi = mido.MidiFile(combined, charset="latin1")  # Rejeita arquivo incompleto.
    if not any(m.type == "note_on" and m.velocity for t in midi.tracks for m in t):
        raise ValueError("Esse MIDI está vazio; escolha outra conversão salva.")
    try:
        data = json.loads((job / "resultado_salvo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = repair_paths(data, job)
    events = result_dir / "silabas.json"
    if not events.exists():
        events.write_text("[]", encoding="utf-8")
    archive = job / "entrega_midi_karaoke.zip"
    valid_archive = False
    if archive.exists():
        try:
            with zipfile.ZipFile(archive) as zipped:
                valid_archive = bool(zipped.namelist())
        except (OSError, zipfile.BadZipFile):
            pass
    if not valid_archive:
        temporary = job / "entrega_recuperada.tmp"
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zipped:
            for file in result_dir.rglob("*"):
                if file.is_file():
                    zipped.write(file, file.relative_to(result_dir))
        temporary.replace(archive)
    sources = sorted(
        p for p in (job / "entrada").glob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )
    vocal_sources = [
        p for p in sources if any(token in p.stem.casefold() for token in ("vocal", "voice", "voz"))
    ]
    source = (
        sources[0]
        if len(sources) == 1
        else vocal_sources[0]
        if len(vocal_sources) == 1
        else job / "sem_audio"
    )
    text = result_dir / "letra_reconhecida.txt"
    karaoke = midi_dir / "karaoke.kar"
    data.update(
        job_dir=str(job),
        result_dir=str(result_dir),
        midi_dir=str(midi_dir),
        combined_mid=str(combined),
        archive=str(archive),
        events_path=str(events),
        karaoke_mid=str(midi_dir / "karaoke.mid") if (midi_dir / "karaoke.mid").exists() else None,
        karaoke_kar=str(karaoke) if karaoke.exists() else None,
        lyrics_txt=str(text) if text.exists() else None,
    )
    saved_source = Path(data.get("source_audio") or job / "sem_audio")
    if not saved_source.is_file():
        expected = str(data.get("source_audio", "")).replace("\\", "/").rsplit("/", 1)[-1]
        exact = job / "entrada" / expected
        data["source_audio"] = str(exact if exact.is_file() else source)
    if data.get("vocals_path") and not Path(data["vocals_path"]).is_file():
        data["vocals_path"] = None
    if not data.get("stems_dir") or not Path(data["stems_dir"]).exists():
        data["stems_dir"] = str(job / "stems_separados")
    data.setdefault("stems_dir", str(job / "stems_separados"))
    data.setdefault("include_stems", False)
    data.setdefault("duration", midi.length)
    try:
        raw_words = (result_dir / "palavras.json").read_bytes()
        try:
            words = json.loads(raw_words.decode("utf-8"))
        except UnicodeDecodeError:
            words = json.loads(raw_words.decode("latin-1"))
        word_count = len(words) if isinstance(words, list) else 0
    except (OSError, ValueError):
        word_count = 0
    data.setdefault("detected_word_count", word_count)
    data.setdefault("preview_seconds", 0)
    data.setdefault(
        "transcription_warning",
        "O MIDI foi salvo; a etapa de letra não chegou a concluir."
        if data.get("saved_status") == "midi_ready" and not karaoke.exists()
        else "",
    )
    return data


def audio_preview(source, job, start=0):
    """Somente 30 s em MP3; evita carregar um WAV inteiro na memória da interface."""
    source, job = Path(source), Path(job)
    start = max(0, int(start))
    target = job / f"previa_editor_{start}.mp3"
    if not target.exists():
        output = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-ss",
                str(start),
                "-i",
                str(source),
                "-t",
                "30",
                "-ar",
                "22050",
                "-ac",
                "1",
                "-b:a",
                "96k",
                str(target),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if output.returncode:
            target.unlink(missing_ok=True)
            raise RuntimeError(
                "Não foi possível preparar a prévia de áudio. Os MIDIs continuam disponíveis."
            )
    return target
