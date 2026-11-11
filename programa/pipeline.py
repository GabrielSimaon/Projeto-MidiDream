"""Pipeline principal do YouTube/áudio para MIDI e karaokê."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile
import tempfile
from collections import deque
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from typing import Callable

import librosa
import mido
import pretty_midi

# Garante acesso aos binários do FFmpeg caso não estejam no PATH global do sistema
tools_candidates = [
    os.environ.get("MIDI_STUDIO_TOOLS", ""),
    str(Path(__file__).resolve().parent / "ferramentas"),
    r"C:\Users\Desktop\AppData\Local\YouTubeMidiStudio\ferramentas",
]
for t_path in tools_candidates:
    if t_path and os.path.isdir(t_path) and t_path not in os.environ.get("PATH", ""):
        os.environ["PATH"] = t_path + os.pathsep + os.environ.get("PATH", "")

from lyrics import build_lyric_events, remap_channels, export_karaoke
from midi_timing import rescale_ppq, make_instrumental
from performance import cache_key, read_cache, save_cache, cpu_threads
from saved_results import save_result_state
from song_names import rename_song_result
from midi_mix import apply_mix, validate_mix
from job_paths import find_moved_input
from musical_style import read_reference
from accompaniment_cleanup import filter_result_midis, key_scale, timed_chords
from live_vocals import parse_crowd_ranges
from music_guide import parse_guide, add_chord_track, validate_required_inputs
from instrument_catalog import extra_kind
from instrument_separator import separate_instruments
from youtube_support import download_tool_args, recoverable_download_error

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".webm"}
Progress = Callable[[str, str], None]


def _notify(callback: Progress | None, stage: str, detail: str) -> None:
    """Atualiza o progresso, se a interface tiver fornecido um callback."""
    if callback:
        callback(stage, detail)


def _run(cmd: list[str], *, cwd: Path | None = None, on_output=None) -> str:
    """Entrega stdout/stderr durante a execução e limita o diagnóstico em memória."""
    tail = deque(maxlen=400)
    with subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env={
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "ORT_DISABLE_TELEMETRY": "1",
            "TF_NUM_INTRAOP_THREADS": str(cpu_threads()),
            "TF_NUM_INTEROP_THREADS": "1",
        },
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        try:
            for line in process.stdout:
                tail.append(line)
                if on_output:
                    on_output(line.strip())
            code = process.wait()
        except BaseException:
            process.terminate()
            process.wait()
            raise
    output = "".join(tail)
    if code:
        raise RuntimeError(f"Comando falhou (código {code}):\n{' '.join(cmd)}\n\n{output[-12000:]}")
    return output


def worker_progress(line, stage, callback):
    """Só transforma percentuais reais do worker em avanço da etapa."""
    match = re.search(r"\[progress\] (\d+)/(\d+) (.*)", line)
    if match:
        current, total = int(match[1]), max(1, int(match[2]))
        _notify(callback, stage, f"{100 * current / total:.0f}% — {match[3]}")
    elif stage == "demucs" and ("seconds/s" in line or "s/seconds" in line):
        percent = re.search(r"(\d+(?:\.\d+)?)%", line)
        if percent:
            _notify(callback, stage, f"{percent[1]}% — separação de instrumentos")
    elif stage == "demucs" and "B/s" in line:
        percent = re.search(r"(\d+(?:\.\d+)?)%", line)
        if percent:
            _notify(callback, "modelo", f"{percent[1]}% — baixando modelo de separação")
    elif stage == "demucs" and ("Downloading" in line or "download" in line.lower()):
        _notify(callback, stage, "Baixando modelo de separação no primeiro uso…")


def transcribe_words(
    audio_path, model_size="base", language="pt", fast=False, progress=None, alignment_text=None
):
    """A memória do modelo é liberada ao terminar o processo filho."""
    with tempfile.TemporaryDirectory(prefix="midi_lyrics_") as directory:
        output = Path(directory) / "words.json"
        cmd = [
            sys.executable,
            "-u",
            str(Path(__file__).with_name("lyrics_worker.py")),
            "--audio",
            str(audio_path),
            "--output",
            str(output),
            "--model",
            model_size,
            "--language",
            language,
        ]
        if alignment_text:
            text_file = Path(directory) / "text.txt"
            text_file.write_text(alignment_text, encoding="utf-8")
            cmd.extend(["--align-text", str(text_file)])
        if fast:
            cmd.append("--fast")

        def report(line):
            if progress and line.startswith("LYRICS_PROGRESS "):
                try:
                    progress(min(1.0, max(0.0, float(line.split()[1]))))
                except (ValueError, IndexError):
                    pass

        try:
            _run(cmd, on_output=report)
        except RuntimeError as exc:
            raise RuntimeError(
                "O reconhecimento da letra foi interrompido. O MIDI já foi salvo. "
                + str(exc)[-500:]
            ) from None
        raw = output.read_bytes()
        try:
            return json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return json.loads(raw.decode("latin-1"))


class YouTubeDownloadError(RuntimeError):
    """Falha de download com instruções úteis e diagnóstico técnico separado."""

    def __init__(self, message, details=""):
        super().__init__(message)
        self.details = details


def normalize_youtube_url(url: str) -> str:
    """Aceita qualquer link de vídeo do YouTube, limpando parâmetros de playlist/rádio."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    valid_hosts = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
    if host not in valid_hosts:
        raise ValueError("Cole o link de um vídeo do YouTube (ex: https://www.youtube.com/watch?v=...)")

    parts = parsed.path.strip("/").split("/")
    video = ""
    if host in ("youtu.be", "www.youtu.be") and parts:
        video = parts[0]
    elif parts and parts[0] == "watch":
        video = parse_qs(parsed.query).get("v", [""])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
        video = parts[1]

    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        raise ValueError("Cole o link de um vídeo do YouTube (ex: https://www.youtube.com/watch?v=...)")
    return "https://www.youtube.com/watch?v=" + video


def download_youtube(url: str, input_dir: Path, browser: str | None = None) -> Path:
    """Usa a sessão local SOMENTE quando o usuário escolhe essa opção na interface.

    Cookies não são exportados para os resultados nem para o ZIP. O yt-dlp
    mantém a sessão em memória e envia os cookies de domínio correspondentes.
    """
    url = normalize_youtube_url(url)
    if browser not in (None, "firefox", "chrome", "edge", "brave"):
        raise ValueError("Navegador não suportado.")
    input_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--compat-options",
        "no-certifi",
        "--no-playlist",
        "--no-progress",
        "--force-overwrites",
        "--no-continue",
        "--socket-timeout",
        "25",
        "--restrict-filenames",
        "--sleep-requests",
        "2",
        "--retries",
        "1",
        "--extractor-retries",
        "1",
        "--concurrent-fragments",
        "1",
        "--extractor-args",
        "youtube:player_client=ios,android,web",
        "-f",
        "bestaudio/best",
        "-x",
        "--audio-format",
        "wav",
        "--audio-quality",
        "0",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-lang",
        "pt,en",
        "--sub-format",
        "vtt",
        "-o",
        str(input_dir / "musica.%(ext)s"),
    ]
    cmd.extend(download_tool_args())
    if browser:
        cmd.extend(["--cookies-from-browser", browser])
    cmd.extend(["--", url])
    try:
        try:
            output = _run(cmd)
        except RuntimeError as initial:
            if not recoverable_download_error(str(initial)):
                raise
            # Uma única recuperação para extrator/EJS antigos; bloqueios de login não entram aqui.
            update_youtube_downloader()
            output = _run(cmd)
    except RuntimeError as exc:
        # Separa o bloqueio do site de erros de sessão ou de processamento MIDI.
        details = str(exc).split("\n\n", 1)[-1][-6000:]
        low = details.casefold()
        if any(
            text in low
            for text in (
                "dpapi",
                "decrypt",
                "cookie database",
                "cookies database",
                "could not copy chrome",
                "could not find firefox",
                "could not find chrome",
                "could not find edge",
            )
        ):
            message = (
                "Não consegui ler a sessão desse navegador. Feche-o e tente novamente. "
                "Se Chrome/Edge impedirem o acesso, escolha Firefox com o YouTube conectado, "
                "ou envie o áudio pela interface."
            )
        elif any(
            text in low
            for text in (
                "429",
                "too many requests",
                "not a bot",
                "confirm you’re",
                "confirm you're",
            )
        ):
            message = (
                "O YouTube bloqueou o download (limite de requisições ou verificação antibot). "
                "Para resolver:\n"
                "1. NÃO use guia anônima (ela não salva login nem cookies no computador).\n"
                "2. Abra o vídeo em uma GUIA NORMAL do seu navegador com sua conta do Google/YouTube conectada.\n"
                "3. Feche o navegador para liberar o arquivo de cookies e, na tela do MidiDream, marque 'Usar minha sessão do navegador'.\n"
                "4. Se o bloqueio de IP persistir, baixe o áudio pelo navegador e selecione a opção 'Arquivo MP3 / áudio'."
            )
        elif any(
            text in low
            for text in (
                "sign in",
                "login required",
                "private video",
                "members-only",
                "age-restricted",
            )
        ):
            message = (
                "Este vídeo exige login ou acesso pela sua conta. Abra-o no navegador e confira "
                "se você consegue assistir. Use a opção de sessão apenas se sua conta tiver acesso."
            )
        elif "ffmpeg not found" in low or "ffprobe not found" in low:
            message = "O FFmpeg não foi encontrado. Feche o programa e abra pelo MidiDream.exe para reparar a instalação."
        elif "audio codec" in low or "invalid data found" in low:
            message = "O download retornou um arquivo de áudio inválido. Confira bloqueios da rede, VPN ou antivírus e tente novamente."
        elif "javascript runtime" in low:
            message = "O componente JavaScript do YouTube não está disponível. Clique em Atualizar acesso ao YouTube e abra o programa pelo executável."
        else:
            message = (
                "Não foi possível baixar esse vídeo. Confira se ele abre no YouTube e use "
                "Atualizar acesso ao YouTube. Você também pode enviar o arquivo de áudio."
            )
        raise YouTubeDownloadError(message, details) from None
    found = input_dir / "musica.wav"
    if not found.is_file():
        raise YouTubeDownloadError("O download não gerou o áudio WAV esperado.", output[-4000:])
    return found


def update_youtube_downloader() -> None:
    """Atualiza apenas o acesso ao YouTube, pelo botão da interface e sem console."""
    manager = os.environ.get("MIDI_STUDIO_UV")
    if manager and Path(manager).is_file():
        cmd = [
            manager,
            "pip",
            "install",
            "--python",
            sys.executable,
            "--upgrade",
            "--pre",
            "yt-dlp[default]",
        ]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--pre", "yt-dlp[default]"]
    try:
        _run(cmd)
        tools = os.environ.get("MIDI_STUDIO_TOOLS")
        if os.name == "nt" and tools:
            from bootstrap import prepare_tools
            prepare_tools(Path(tools).parent, update_deno=True)
    except RuntimeError as exc:
        raise RuntimeError(
            "Não foi possível atualizar o acesso ao YouTube. Confira a internet e tente novamente."
        ) from exc


def separate_stems(
    audio_path: Path,
    separated_root: Path,
    model: str = "htdemucs",
    device: str = "cpu",
    speed: str = "balanced",
    progress=None,
) -> Path:
    """Perfis rápidos reduzem sobreposição; hdemucs_mmi evita o transformer."""
    if model not in ("htdemucs", "htdemucs_ft", "htdemucs_6s", "hdemucs_mmi"):
        raise ValueError("Modelo Demucs inválido.")
    if speed == "fast":
        model = "hdemucs_mmi"
    separated_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).with_name("separate_worker.py")),
        "-n",
        model,
        "-d",
        device,
        "-o",
        str(separated_root),
    ]
    if speed == "fast":
        cmd.extend(["--shifts", "0", "--overlap", "0.05", "--segment", "6"])
    elif speed == "balanced":
        cmd.extend(["--shifts", "0", "--overlap", "0.15", "--segment", "7"])
    elif speed == "quality":
        cmd.extend(["--shifts", "0", "--overlap", "0.15", "--segment", "7"])
    cmd.append(str(audio_path))
    output = _run(cmd, on_output=lambda line: worker_progress(line, "demucs", progress))
    candidates = sorted(p.parent for p in separated_root.rglob("vocals.wav"))
    if not candidates:
        raise RuntimeError("O Demucs não gerou as faixas.\n" + output[-6000:])
    return candidates[0]


def _find_audio_duration(path: Path) -> float:
    """Consulta a duração do arquivo com FFprobe."""
    return float(librosa.get_duration(path=str(path)))


def _locate_vocals(stems_dir: Path) -> Path | None:
    """Procura a faixa de voz entre os stems disponíveis."""
    for p in stems_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            name = p.stem.lower()
            if any(x in name for x in ("vocal", "voz", "voice")):
                return p
    return None


def classify_stem_name(filename: str) -> str:
    """Classifica nomes enviados pelo usuário em grupos de instrumentos."""
    extra = extra_kind(filename)
    if extra:
        return extra
    low = Path(filename).stem.lower()
    if any(k in low for k in ("drum", "bateria", "percuss")):
        return "drums"
    if any(k in low for k in ("bass", "baixo")):
        return "bass"
    if any(k in low for k in ("vocal", "voz", "voice")):
        return "vocals"
    if any(k in low for k in ("guitar", "violao", "violão", "guitarra")):
        return "guitar"
    if any(k in low for k in ("piano", "keys", "teclado")):
        return "piano"
    if "other" in low or "accomp" in low or "acompanh" in low:
        return "other"
    return "unknown"


def _is_uploaded_stem_set(files: list[Path]) -> bool:
    # O modo explícito também aceita uma única faixa isolada.
    """Verifica se os arquivos já representam faixas de instrumentos separadas."""
    recognized = [classify_stem_name(p.name) for p in files]
    return bool(files) and "unknown" not in recognized


def prepare_uploaded_stems(files: list[Path], output: Path) -> Path:
    """Reúne arquivos do mesmo grupo sem demixing, mantendo o zero e a maior duração."""
    groups = {}
    for file in files:
        groups.setdefault(classify_stem_name(file.name), []).append(file)
    output.mkdir(parents=True, exist_ok=True)
    for kind, paths in groups.items():
        if len(paths) == 1:
            shutil.copy2(paths[0], output / (kind + paths[0].suffix.lower()))
            continue
        cmd = ["ffmpeg", "-y", "-nostdin"]
        for file in paths:
            cmd.extend(["-i", str(file)])
        cmd.extend(
            [
                "-filter_complex",
                f"amix=inputs={len(paths)}:duration=longest:dropout_transition=0:normalize=1",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(output / (kind + ".wav")),
            ]
        )
        _run(cmd)
    return output


def _zip_results(
    job_dir: Path, result_dir: Path, include_stems: bool, stems_dir: Path | None
) -> Path:
    """Empacota os resultados e, se solicitado, os stems de áudio."""
    zip_path = job_dir / "entrega_midi_karaoke.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in result_dir.rglob("*"):
            if path.is_file() and path.name != zip_path.name:
                zf.write(path, path.relative_to(result_dir))
        if include_stems and stems_dir and stems_dir.exists():
            for path in stems_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, Path("stems") / path.relative_to(stems_dir))
    return zip_path


def run_pipeline(
    job_dir: str | Path,
    *,
    render_samples: bool = False,
    youtube_url: str = "",
    youtube_browser: str | None = None,
    audio_path: str | Path | None = None,
    uploaded_stems_dir: str | Path | None = None,
    separate: bool = True,
    demucs_model: str = "htdemucs",
    grid: int = 0,
    ppq: int = 480,
    vocal_program: int = 52,
    device: str = "cpu",
    recognize_lyrics: bool = True,
    timed_lyrics: list[dict] | None = None,
    bpm_override: float | None = None,
    whisper_model: str = "base",
    language: str = "pt",
    manual_lyrics: str = "",
    include_stems_in_zip: bool = False,
    progress: Progress | None = None,
    speed: str = "balanced",
    cache_root: str | Path | None = None,
    preview_seconds: int = 0,
    quick_pitch: bool = False,
    chord_accompaniment: bool = False,
    live_vocals: str = "off",
    crowd_ranges: str = "",
    song_title: str = "",
    organize_folder: bool = False,
    mix_settings: dict | None = None,
    isolate_piano: bool = False,
    complete_instruments: bool = False,
    accurate_lyrics: bool = True,
    note_cleanup: str = "musical",
    compact_accompaniment: bool = False,
    rhythm_lock: bool = True,
    drum_grid: int = 16,
    vocal_monophonic: bool = True,
    align_lyrics: bool = False,
    musical_reference: str | Path | None = None,
    require_guide: bool = False,
    accompaniment_filter: str = "chords",
    harmonic_correction: bool = True,
    tonality: str | None = None,
    solo_instrument: str = "piano",
) -> dict:
    """Executa processamento e retorna todos os caminhos relevantes."""
    # Etapa 1: valida antes de criar arquivos, baixar áudio ou carregar modelos.
    if accompaniment_filter not in ("off", "gentle", "balanced", "strict", "chords"):
        raise ValueError("Filtro de acompanhamento inválido.")
    key_scale(tonality)
    if drum_grid not in (8, 16, 32):
        raise ValueError("A grade da bateria deve ser 8, 16 ou 32.")
    if require_guide:
        bpm_override = validate_required_inputs(manual_lyrics, bpm_override, require_bpm=True)
    if mix_settings is None and speed == "express":
        mix_settings = {"other": 100}
    mix_settings = validate_mix(mix_settings)
    style_profile = read_reference(musical_reference) if musical_reference else None
    if style_profile:
        compact_accompaniment = style_profile["compact"]
        if bpm_override is None and style_profile.get("bpm"):
            bpm_override = style_profile["bpm"]
    if complete_instruments:
        compact_accompaniment = False
    if compact_accompaniment:
        isolate_piano = False
        if demucs_model == "htdemucs_6s":
            demucs_model = "htdemucs"
    if note_cleanup not in ("musical", "auto", "gentle", "off"):
        raise ValueError("Modo de limpeza de notas inválido.")
    if isolate_piano and separate and not uploaded_stems_dir and speed != "express":
        demucs_model = "htdemucs_6s"
    excluded = parse_crowd_ranges(crowd_ranges)
    if live_vocals not in ("off", "balanced", "strict"):
        raise ValueError("Modo de voz ao vivo inválido.")
    manual_lyrics, chord_marks = parse_guide(manual_lyrics)
    if speed not in ("fast", "balanced", "quality", "express") or preview_seconds not in (0, 30):
        raise ValueError("Perfil ou duração de prévia inválidos.")
    if speed == "express":
        separate = False
    if (live_vocals != "off" or excluded) and not separate and not uploaded_stems_dir:
        raise ValueError(
            "Para filtrar plateia, ative a separação de instrumentos ou envie a faixa de voz separada. O modo Express não isola voz."
        )
    if grid not in (0, 4, 8, 16, 32) or ppq not in (480, 960):
        raise ValueError("Grade ou PPQ inválidos.")
    if device not in ("cpu", "cuda") or vocal_program not in (52, 73):
        raise ValueError("Dispositivo ou timbre vocal inválido.")
    if bpm_override is not None and not 30 <= float(bpm_override) <= 300:
        raise ValueError("O BPM manual deve estar entre 30 e 300.")
    if timed_lyrics is not None:
        if not isinstance(timed_lyrics, list) or not timed_lyrics:
            raise ValueError("O JSON de letra precisa conter uma lista de eventos.")
        for row in timed_lyrics:
            if (
                not isinstance(row, dict)
                or "seconds" not in row
                or not isinstance(row.get("text"), str)
            ):
                raise ValueError(
                    "Cada evento JSON precisa conter seconds (número) e text (string)."
                )
            seconds = float(row["seconds"])
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("Use segundos finitos e não negativos no JSON.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("Instale FFmpeg e FFprobe e adicione-os ao PATH antes de iniciar.")
    job = Path(job_dir).resolve()
    if job.exists() and any(job.iterdir()):
        # A UI cria entrada/ antes da chamada; as outras pastas indicam um job antigo.
        if any((job / name).exists() for name in ("resultados", "motor_midi", "stems_separados")):
            raise ValueError("Esta pasta já tem resultados. Escolha uma pasta nova.")
    job.mkdir(parents=True, exist_ok=True)
    input_dir = job / "entrada"
    separated_root = job / "stems_separados"
    engine_out = job / "motor_midi"
    result_dir = job / "resultados"
    midi_dir = result_dir / "midi"
    input_dir.mkdir(exist_ok=True)
    result_dir.mkdir(exist_ok=True)
    midi_dir.mkdir(parents=True, exist_ok=True)

    stem_files = []
    if uploaded_stems_dir:
        stem_root = Path(uploaded_stems_dir).resolve()
        if not stem_root.is_dir():
            raise ValueError("A pasta de faixas separadas não foi encontrada.")
        stem_files = sorted(
            p for p in stem_root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS
        )
        if not _is_uploaded_stem_set(stem_files):
            raise ValueError(
                "Selecione pelo menos uma faixa e identifique os grupos: voz, baixo, bateria, piano, guitarra ou outros."
            )
        if youtube_url.strip():
            raise ValueError("Escolha uma única fonte: YouTube ou faixas separadas.")
    _notify(progress, "entrada", "Preparando áudio de entrada…")
    if youtube_url.strip():
        source_audio = download_youtube(youtube_url.strip(), input_dir, browser=youtube_browser)
    elif audio_path:
        source_audio = find_moved_input(audio_path).resolve()
        if not source_audio.is_file():
            raise FileNotFoundError(f"Arquivo de áudio não encontrado: {source_audio}")
    elif stem_files:
        source_audio = _locate_vocals(stem_root) or stem_files[0]
    else:
        raise ValueError("Forneça um link, uma música em MP3/áudio ou faixas separadas.")

    if not song_title.strip() and youtube_url.strip():
        try:
            song_title = json.loads(
                (input_dir / "musica.info.json").read_text(encoding="utf-8")
            ).get("title", "")
        except (OSError, ValueError):
            pass
    song_title = (song_title or source_audio.stem).strip()
    (job / "musica.json").write_text(
        json.dumps({"song_title": song_title}, ensure_ascii=False), encoding="utf-8"
    )

    if preview_seconds:
        _notify(progress, "entrada", "Preparando prévia dos primeiros 30 segundos…")
        preview_dir = job / "previa_30s"
        preview_dir.mkdir(exist_ok=True)
        sources = stem_files if stem_files else [source_audio]
        for index, file in enumerate(sources):
            target = preview_dir / (f"{index}_{file.stem}.wav")
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-nostdin",
                    "-i",
                    str(file),
                    "-t",
                    "30",
                    "-ar",
                    "44100",
                    str(target),
                ]
            )
        if stem_files:
            stem_root = preview_dir
            stem_files = sorted(preview_dir.glob("*.wav"))
            source_audio = _locate_vocals(stem_root) or stem_files[0]
        else:
            source_audio = next(preview_dir.glob("*.wav"))

    # Etapa 2: reutiliza stems enviados ou separa o áudio completo.
    stems_dir: Path | None = stem_root if stem_files else None
    if stems_dir is not None:
        _notify(
            progress,
            "faixas",
            f"Usando {len(stem_files)} faixa(s) pronta(s). Separação de instrumentos ignorada.",
        )
        if len({classify_stem_name(p.name) for p in stem_files}) < len(stem_files):
            _notify(
                progress, "faixas", "Reunindo arquivos do mesmo grupo, sem separar instrumentos…"
            )
            stems_dir = prepare_uploaded_stems(stem_files, job / "faixas_prontas")
    if stems_dir is None and separate:
        separation_speed = (
            "balanced" if demucs_model == "htdemucs_6s" and speed == "fast" else speed
        )
        chosen_model = "hdemucs_mmi" if separation_speed == "fast" else demucs_model
        required = ["vocals.wav", "bass.wav", "drums.wav", "other.wav"]
        if chosen_model == "htdemucs_6s":
            required += ["piano.wav", "guitar.wav"]
        stem_key = (
            cache_key(
                [("mix", source_audio)],
                {"version": 1, "model": chosen_model, "speed": separation_speed},
            )
            if cache_root
            else None
        )
        cached = read_cache(Path(cache_root) / "stems", stem_key, required) if cache_root else None
        if cached:
            stems_dir = cached
            _notify(
                progress, "demucs", "100% — faixas recuperadas do cache; separação já concluída"
            )
        else:
            _notify(progress, "demucs", f"Separando instrumentos ({chosen_model})…")
            stems_dir = separate_stems(
                source_audio,
                separated_root,
                model=demucs_model,
                device=device,
                speed=separation_speed,
                progress=progress,
            )
            if cache_root and all((stems_dir / name).is_file() for name in required):
                save_cache(
                    Path(cache_root) / "stems", stem_key, [stems_dir / name for name in required]
                )
    elif stems_dir is None:
        # Permite transcrever um único áudio, mas a qualidade é inferior à de stems separados.
        stems_dir = source_audio

    if complete_instruments:
        _notify(progress, "instrumentos", "Reconhecendo quais instrumentos existem no acompanhamento…")
        stems_dir = separate_instruments(stems_dir, job / "faixas_instrumentos",
            Path(os.environ.get("HF_HOME", str(job.parent / "modelos"))).parent / "audiosep",
            _run, progress, verify_known=uploaded_stems_dir is None)
        shutil.copy2(stems_dir / "instrumentos.json", result_dir / "instrumentos.json")
        shutil.copy2(stems_dir.parent / "acompanhamento_nao_identificado.wav", result_dir / "acompanhamento_nao_identificado.wav")

    # Detecção automática de BPM com alta precisão quando não informado manualmente
    if bpm_override is None or float(bpm_override) <= 0:
        _notify(progress, "tempo", "Detectando BPM com alta precisão diretamente do áudio…")
        from bpm_detector import detect_precise_bpm
        drums_candidate = (stems_dir / "drums.wav") if stems_dir.is_dir() and (stems_dir / "drums.wav").is_file() else None
        detected_info = detect_precise_bpm(source_audio, drums_path=drums_candidate)
        bpm_override = float(detected_info["bpm"])
        _notify(progress, "tempo", f"BPM detectado automaticamente: {bpm_override:.2f}")

    # Normalização e estabilização de volume nos stems antes da conversão para MIDI
    if stems_dir.is_dir():
        _notify(progress, "filtro", "Normalizando faixas e estabilizando áudio dos stems…")
        try:
            from bleed_cleaner import normalize_audio_stems
            normalize_audio_stems(stems_dir)
        except Exception as exc:
            print(f"[aviso] Normalização de áudio nos stems: {exc}")

    _notify(progress, "midi", "Transcrevendo as faixas para MIDI…")
    cmd = [
        sys.executable,
        str(Path(__file__).with_name("audio2midi_core.py")),
        str(stems_dir),
        "-o",
        str(engine_out),
        "--grid",
        str(grid),
    ]
    if speed in ("fast", "express"):
        cmd.append("--fast")
    if quick_pitch:
        cmd.append("--quick-pitch")
    cmd.extend(["--live-vocals", live_vocals, "--note-cleanup", note_cleanup])
    cmd.extend(["--drum-grid", str(drum_grid)])
    if not rhythm_lock:
        cmd.append("--no-rhythm-lock")
    if not vocal_monophonic:
        cmd.append("--vocal-polyphonic")
    if compact_accompaniment:
        cmd.append("--compact")
    if style_profile:
        profile_path = job / "perfil_referencia.json"
        profile_path.write_text(json.dumps(style_profile, ensure_ascii=False), encoding="utf-8")
        cmd.extend(["--style-profile", str(profile_path)])
    if excluded:
        cmd.extend(["--crowd-ranges", crowd_ranges])
    if not uploaded_stems_dir:
        cmd.extend(["--reference", str(source_audio)])
    if bpm_override is not None and float(bpm_override) > 0:
        if not (30.0 <= float(bpm_override) <= 300.0):
            raise ValueError("O BPM manual deve estar entre 30 e 300.")
        cmd.extend(["--bpm", str(float(bpm_override))])
    audio_files = (
        sorted(p for p in stems_dir.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
        if stems_dir.is_dir()
        else [stems_dir]
    )
    keyed_files = [(classify_stem_name(p.name), p) for p in audio_files]
    if (live_vocals != "off" or excluded) and not any(kind == "vocals" for kind, _ in keyed_files):
        raise ValueError(
            "Não encontrei a faixa de voz. Marque uma faixa como Voz ou ative a separação de instrumentos."
        )
    if not uploaded_stems_dir:
        keyed_files.append(("bpm_reference", source_audio))
    midi_key = (
        cache_key(
            keyed_files,
            {
                "version": 16,
                "drum_grid": drum_grid,
                "rhythm_lock": rhythm_lock,
                "vocal_monophonic": vocal_monophonic,
                "compact": compact_accompaniment,
                "style_profile": style_profile,
                "note_cleanup": note_cleanup,
                "live_vocals": live_vocals,
                "crowd_ranges": excluded,
                "quick_pitch": quick_pitch,
                "speed": speed,
                "grid": grid,
                "bpm": bpm_override,
            },
        )
        if cache_root
        else None
    )
    # Etapa 3: reaproveita notas do cache somente se áudio e parâmetros coincidirem.
    cached_midi = (
        read_cache(Path(cache_root) / "midi", midi_key, ["completo.mid"]) if cache_root else None
    )
    engine_midi_dir = engine_out / "midi"
    if cached_midi:
        engine_midi_dir.mkdir(parents=True, exist_ok=True)
        for file in list(cached_midi.glob("*.mid")) + list(cached_midi.glob("relatorio_*.json")):
            shutil.copy2(file, engine_midi_dir / file.name)
        core_log = "Notas MIDI recuperadas do cache."
        _notify(progress, "midi", "100% — notas MIDI recuperadas do cache")
    else:
        core_log = _run(cmd, on_output=lambda line: worker_progress(line, "midi", progress))
        if cache_root and (engine_midi_dir / "completo.mid").is_file():
            save_cache(
                Path(cache_root) / "midi",
                midi_key,
                list(engine_midi_dir.glob("*.mid"))
                + list(engine_midi_dir.glob("relatorio_*.json")),
            )
    for report_file in engine_midi_dir.glob("relatorio_*.json"):
        shutil.copy2(report_file, result_dir / report_file.name)

    combined = engine_midi_dir / "completo.mid"
    if not combined.exists():
        raise RuntimeError("O motor não gerou completo.mid. Log:\n" + core_log[-10000:])

    # Copia os MIDIs originais do motor_midi para resultados/midi, mantendo motor_midi intacto como base limpa
    for file in engine_midi_dir.glob("*.mid"):
        dst = midi_dir / file.name
        shutil.copy2(file, dst)
        remap_channels(dst)
        midi = mido.MidiFile(dst, charset="latin1")
        rescale_ppq(midi, ppq)
        for track in midi.tracks:
            for msg in track:
                if msg.type == "program_change" and msg.channel == 1:
                    msg.program = vocal_program
        midi.save(dst)

    combined_out = midi_dir / "completo.mid"
    remap_channels(combined_out)
    instrumental = midi_dir / "instrumental.mid"
    make_instrumental(combined_out, instrumental)

    duration = (
        max(
            (
                _find_audio_duration(p)
                for p in stems_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS
            ),
            default=0.0,
        )
        if uploaded_stems_dir
        else _find_audio_duration(source_audio)
    )
    events_path = result_dir / "silabas.json"
    events_path.write_text("[]", encoding="utf-8")
    save_result_state(
        {
            "song_title": song_title,
            "job_dir": job,
            "source_audio": source_audio,
            "stems_dir": stems_dir,
            "duration": duration,
            "include_stems": include_stems_in_zip,
            "preview_seconds": preview_seconds,
            "speed": speed,
        },
        status="midi_ready",
    )
    _notify(progress, "letra", "MIDI salvo. Preparando a letra…")
    vocal_path = _locate_vocals(stems_dir) if stems_dir.is_dir() else None
    transcription_source = vocal_path or source_audio
    detected_words: list[dict] = []
    transcription_warning = ""
    can_recognize = not uploaded_stems_dir or vocal_path is not None
    if recognize_lyrics and timed_lyrics is None and not can_recognize:
        transcription_warning = "Sem faixa de voz: o reconhecimento de letra foi ignorado; o MIDI instrumental será gerado."
    if (
        recognize_lyrics
        and timed_lyrics is None
        and can_recognize
        and not (align_lyrics and manual_lyrics.strip())
    ):
        try:
            word_key = (
                cache_key(
                    [("voice", transcription_source)],
                    {
                        "version": 2,
                        "model": whisper_model,
                        "language": language,
                        "fast": not accurate_lyrics,
                    },
                )
                if cache_root
                else None
            )
            cached_words = (
                read_cache(Path(cache_root) / "lyrics", word_key, ["palavras.json"])
                if cache_root
                else None
            )
            if cached_words:
                try:
                    detected_words = json.loads(
                        (cached_words / "palavras.json").read_text(encoding="utf-8")
                    )
                    if not isinstance(detected_words, list):
                        cached_words = None
                except (OSError, ValueError):
                    cached_words = None
            if cached_words:
                _notify(progress, "letra", "100% — letra recuperada do cache")
            else:
                detected_words = transcribe_words(
                    transcription_source,
                    model_size=whisper_model,
                    language=language,
                    fast=not accurate_lyrics,
                    progress=lambda fraction: _notify(
                        progress, "letra", f"{fraction * 100:.0f}% — reconhecendo letra"
                    ),
                )
                if cache_root:
                    words_file = result_dir / "palavras.json"
                    words_file.write_text(
                        json.dumps(detected_words, ensure_ascii=False), encoding="utf-8"
                    )
                    save_cache(Path(cache_root) / "lyrics", word_key, [words_file])
        except Exception as exc:
            transcription_warning = f"Não foi possível executar o Whisper: {exc}"

    # Etapa 4: encontra as palavras no canto, inclusive nos refrões repetidos.
    if align_lyrics and can_recognize:
        alignment_text = manual_lyrics.strip()
        if not alignment_text and timed_lyrics:
            alignment_text = "".join(
                "\n" if row["text"] == "[QUEBRA]" else row["text"] for row in timed_lyrics
            )
        if not alignment_text and detected_words:
            alignment_text = " ".join(w["text"] for w in detected_words)
        if alignment_text:
            try:
                _notify(progress, "letra", "Alinhando palavras com o canto…")
                align_key = (
                    cache_key(
                        [("voice", transcription_source)],
                        {
                            "version": 1,
                            "text": alignment_text,
                            "model": whisper_model,
                            "language": language,
                        },
                    )
                    if cache_root
                    else None
                )
                cached_align = (
                    read_cache(Path(cache_root) / "alignment", align_key, ["palavras.json"])
                    if cache_root
                    else None
                )
                aligned_words = (
                    json.loads((cached_align / "palavras.json").read_text(encoding="utf-8"))
                    if cached_align
                    else transcribe_words(
                        transcription_source,
                        model_size=whisper_model,
                        language=language,
                        alignment_text=alignment_text,
                        progress=lambda f: _notify(
                            progress, "letra", f"{f * 100:.0f}% — alinhando palavras com a voz"
                        ),
                    )
                )
                if not aligned_words:
                    raise ValueError("Nenhuma palavra alinhada.")
                detected_words = aligned_words
                manual_lyrics = alignment_text
                timed_lyrics = None
                if cache_root and not cached_align:
                    aligned_file = result_dir / "palavras.json"
                    aligned_file.write_text(
                        json.dumps(aligned_words, ensure_ascii=False), encoding="utf-8"
                    )
                    save_cache(Path(cache_root) / "alignment", align_key, [aligned_file])
            except Exception as exc:
                transcription_warning += f" Alinhamento acústico não concluído: {exc}"

    # Filtra após alinhar a letra: as cifras já podem ter tempos acústicos confiáveis.
    _notify(progress, "filtro", "Removendo notas fantasma do acompanhamento…")
    if not tonality and chord_marks:
        from music_guide import infer_tonality_from_chords
        tonality = infer_tonality_from_chords(chord_marks)
        if tonality:
            _notify(progress, "harmonia", f"Tonalidade identificada pelas cifras: {tonality}")

    filtered = filter_result_midis(
        midi_dir,
        result_dir,
        text=manual_lyrics,
        marks=chord_marks,
        detected_words=detected_words,
        duration=duration,
        level="balanced" if tonality and accompaniment_filter == "chords" else accompaniment_filter,
        correct_pitch=harmonic_correction,
        key=tonality,
    )
    summary = filtered["files"].get("completo.mid", {})
    _notify(
        progress,
        "filtro",
        f"100% — {summary.get('removed', 0)} notas removidas; {summary.get('corrected', 0)} corrigidas",
    )
    make_instrumental(combined_out, midi_dir / "instrumental.mid")

    chords_timed = timed_chords(manual_lyrics, chord_marks, detected_words, duration)
    effective_key = tonality or (infer_tonality_from_chords(chord_marks) if chord_marks else None)
    if effective_key:
        from harmonic_lock import lock_result
        harmony = lock_result(midi_dir, result_dir, effective_key, chords_timed or ())
        fixed = harmony["files"].get("completo.mid", {}).get("corrected", 0)
        _notify(progress, "harmonia", f"Harmonia na escala ({effective_key}): {fixed} notas alinhadas; bateria preservada.")
        make_instrumental(combined_out, midi_dir / "instrumental.mid")

    # Sincronização inteligente para vídeos de karaokê e playbacks instrumentais
    try:
        from karaoke_sync import is_karaoke_source, align_karaoke_lyrics_rhythmically, extract_intro_from_info, parse_vtt_subtitles
        vocal_rms = None
        if stems_dir.is_dir() and (stems_dir / "vocals.wav").is_file():
            from auto_mix import measure_active_rms
            vocal_rms = measure_active_rms(stems_dir / "vocals.wav")

        is_karaoke = is_karaoke_source(song_title, detected_words, vocal_rms=vocal_rms)

        # 1. Tenta usar legendas (.vtt) baixadas do YouTube caso existam
        vtt_candidates = list(input_dir.glob("*.vtt"))
        if vtt_candidates and (is_karaoke or not detected_words):
            vtt_words = parse_vtt_subtitles(vtt_candidates[0])
            if vtt_words:
                detected_words = vtt_words
                _notify(progress, "letra", "Letra sincronizada diretamente pelas legendas do YouTube!")

        # 2. Se for karaokê/playback e a letra foi fornecida manualmente (sem alinhamento acústico prévio)
        if (is_karaoke or not detected_words) and not align_lyrics and manual_lyrics.strip():
            info_json = input_dir / "musica.info.json"
            intro_sec = extract_intro_from_info(info_json)
            karaoke_aligned = align_karaoke_lyrics_rhythmically(
                manual_lyrics,
                duration=duration,
                bpm=float(bpm_override or 120.0),
                intro_seconds=intro_sec,
            )
            if karaoke_aligned:
                detected_words = karaoke_aligned
                _notify(progress, "letra", "Modo Karaokê: letra sincronizada compasso a compasso com o playback!")
    except Exception as exc:
        print(f"[aviso] Sincronização de karaokê: {exc}")

    # Oculta completamente símbolos de acordes da letra do karaokê
    from music_guide import is_chord_symbol
    if detected_words:
        detected_words = [w for w in detected_words if not is_chord_symbol(w.get("text", ""))]

    # Detecção e separação de solos para instrumento de solo dedicado (Piano Solo, Guitarra Elétrica ou Violão)
    actual_solo = solo_instrument or (mix_settings.get("solo_instrument") if mix_settings else "piano") or "piano"
    solo_intervals = []
    try:
        from solo_detector import detect_solo_intervals, split_piano_solo
        solo_intervals = detect_solo_intervals(
            duration=duration,
            detected_words=detected_words,
            guide_text=manual_lyrics,
        )
        solo_report = split_piano_solo(
            piano_midi_path=midi_dir / "piano.mid",
            other_midi_path=midi_dir / "other.mid",
            drums_midi_path=midi_dir / "drums.mid",
            solo_intervals=solo_intervals,
            output_solo_path=midi_dir / "piano_solo.mid",
            output_piano_path=midi_dir / "piano.mid",
            bpm=float(bpm_override or 120.0),
            key=effective_key,
            chords=chords_timed or (),
            solo_instrument=actual_solo,
        )
        if solo_report.get("solo_notes", 0) > 0:
            _notify(
                progress,
                "solo",
                f"Solo melódico isolado ({actual_solo}): {solo_report['solo_notes']} notas de solo "
                f"(Começo: {solo_report['comeco_notes']}, Meio: {solo_report['meio_notes']}, Final: {solo_report['final_notes']}). "
                f"Base e outros limpos sem duplicar solo.",
            )
    except Exception as exc:
        print(f"[aviso] Separação de Solo: {exc}")

    # Aprimoramento e consolidação dos teclados e instrumentos de acompanhamento
    try:
        from keyboard_enhancer import enhance_keyboards
        kb_report = enhance_keyboards(
            midi_dir=midi_dir,
            engine_midi_dir=engine_midi_dir,
            bpm=float(bpm_override or 120.0),
            chords=chords_timed or (),
            duration=duration,
            key=effective_key,
            solo_intervals=solo_intervals,
            allow_synthetic=False,
            solo_instrument=actual_solo,
        )
        _notify(
            progress,
            "teclados",
            f"Separação multi-instrumental concluída [Solo: {kb_report.get('solo_instrument', 'Piano Solo')}] ({kb_report.get('piano_notes', 0)} notas piano base, {kb_report.get('piano_solo_notes', 0)} notas solo, {kb_report.get('guitar_notes', 0)} notas violão, {kb_report.get('accordion_notes', 0)} notas sanfona, {kb_report.get('pad_notes', 0)} notas pad)",
        )
        make_instrumental(combined_out, midi_dir / "instrumental.mid")
    except Exception as exc:
        print(f"[aviso] Aprimoramento de teclados: {exc}")

    # Balanceamento automático de volumes dos instrumentos
    try:
        from auto_mix import calculate_relative_volumes, apply_auto_mix
        volumes = calculate_relative_volumes(stems_dir if stems_dir.is_dir() else input_dir)
        apply_auto_mix(midi_dir, volumes)
        _notify(progress, "mix", f"Volumes ajustados automaticamente: piano={volumes.get('piano')}, piano_solo={volumes.get('piano_solo')}, other={volumes.get('other')}")
        make_instrumental(combined_out, midi_dir / "instrumental.mid")
    except Exception as exc:
        print(f"[aviso] Balanceamento de volumes: {exc}")

    if chord_accompaniment and chord_marks:
        add_chord_track(combined_out, manual_lyrics, chord_marks, detected_words, duration)
        make_instrumental(combined_out, midi_dir / "instrumental.mid")

    # Etapa 5: converte palavras em eventos de sílabas e exporta MIDI/KAR.
    lyric_events, plain_lyrics, lyric_method = build_lyric_events(
        detected_words=detected_words,
        manual_text=manual_lyrics,
        duration=duration,
        language=language,
    )
    if timed_lyrics is not None:
        lyric_events = [
            (float(row["seconds"]), "\r\n" if row["text"] == "[QUEBRA]" else str(row["text"]))
            for row in timed_lyrics
        ]
        plain_lyrics = "".join(text for _, text in lyric_events)
        lyric_method = "tempos importados do JSON; sem interpolação automática"
    elif manual_lyrics.strip() and not detected_words:
        transcription_warning += " Sem âncoras vocais: a letra digitada foi distribuída pela duração; ajuste os tempos no editor."
    elif align_lyrics and detected_words:
        lyric_method = "palavras alinhadas ao áudio; sílabas interpoladas dentro de cada palavra"
    elif not recognize_lyrics:
        lyric_method = "reconhecimento de letra desativado"
    (result_dir / "palavras.json").write_text(
        json.dumps(detected_words, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lyrics_txt = result_dir / "letra_reconhecida.txt"
    if plain_lyrics:
        lyrics_txt.write_text(plain_lyrics.replace("\r\n", "\n") + "\n", encoding="utf-8")

    karaoke_created = False
    if lyric_events:
        rows = [
            {"seconds": t, "text": "[QUEBRA]" if text == "\r\n" else text}
            for t, text in lyric_events
        ]
        lyric_events = export_karaoke(combined_out, rows, midi_dir)
        karaoke_created = True

    events_path = result_dir / "silabas.json"
    events_path.write_text(
        json.dumps(
            [
                {"seconds": round(t, 6), "text": "[QUEBRA]" if text == "\r\n" else text}
                for t, text in lyric_events
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (result_dir / "log_transcricao.txt").write_text(
        "LOG DO PROCESSAMENTO\n\n"
        + "Entrada: "
        + (youtube_url.strip() or str(source_audio))
        + "\n"
        + "Pasta de stems: "
        + str(stems_dir)
        + "\n"
        + f"Duração aproximada: {duration:.2f} segundos\n"
        + f"Palavras identificadas: {len(detected_words)}\n"
        + f"Eventos de letra no MIDI: {len(lyric_events)}\n"
        + f"Resolução MIDI: {ppq} PPQ\n"
        + f"Quantização: {grid or 'desativada'}\n"
        + f"Filtro ao vivo: {live_vocals}; trechos manuais: {excluded}\n"
        + f"Perfil: {speed}; prévia: {preview_seconds or 'música completa'}\n"
        + "Andamento constante estimado/manual; variações de BPM do áudio não são inferidas.\n"
        + f"Método da letra: {lyric_method}\n"
        + ("Aviso: " + transcription_warning + "\n" if transcription_warning else "")
        + "\nLOG DO MOTOR MIDI:\n"
        + core_log,
        encoding="utf-8",
    )

    # Etapa 6: aplica volumes e monta o ZIP que será oferecido para download.
    # O programa GM pertence à identidade detectada do stem; a mixagem só deve
    # alterar volumes, nunca trocar guitarra, sanfona ou outro instrumento por piano.
    final_mix_settings = dict(mix_settings or {})
    final_mix_settings["preserve_programs"] = True
    apply_mix(result_dir, final_mix_settings)

    # Garante alinhamento métrico exato (100% cravado no BPM e sem desvios de ticks)
    try:
        from midi_timing import quantize_midi_file_ticks
        for midi_f in midi_dir.glob("*.mid"):
            quantize_midi_file_ticks(midi_f)
        if (midi_dir / "karaoke.kar").is_file() and (midi_dir / "karaoke.mid").is_file():
            (midi_dir / "karaoke.kar").write_bytes((midi_dir / "karaoke.mid").read_bytes())
    except Exception as exc:
        print(f"[aviso] Quantização estrita de ticks: {exc}")

    if render_samples:
        from sample_audio import render_result
        render_result(result_dir, progress)
    archive = _zip_results(
        job, result_dir, include_stems_in_zip, stems_dir if stems_dir.is_dir() else None
    )
    _notify(progress, "finalizado", "Processamento concluído.")
    result = {
        "render_samples": render_samples,
        "reference_name": style_profile.get("name") if style_profile else None,
        "note_cleanup": note_cleanup,
        "mix_settings": mix_settings,
        "song_title": song_title,
        "job_dir": job,
        "source_audio": source_audio,
        "stems_dir": stems_dir,
        "vocals_path": vocal_path,
        "result_dir": result_dir,
        "midi_dir": midi_dir,
        "combined_mid": combined_out,
        "karaoke_mid": midi_dir / "karaoke.mid" if karaoke_created else None,
        "karaoke_kar": midi_dir / "karaoke.kar" if karaoke_created else None,
        "piano_solo_mid": midi_dir / "piano_solo.mid" if (midi_dir / "piano_solo.mid").is_file() else None,
        "events_path": events_path,
        "include_stems": include_stems_in_zip,
        "lyrics_txt": lyrics_txt if lyrics_txt.exists() else None,
        "archive": archive,
        "duration": duration,
        "speed": speed,
        "preview_seconds": preview_seconds,
        "detected_word_count": len(detected_words),
        "lyric_method": lyric_method,
        "transcription_warning": transcription_warning,
        "core_log": core_log,
    }
    save_result_state(result)
    if organize_folder:
        try:
            result = rename_song_result(result, song_title)
        except OSError as exc:
            result["transcription_warning"] += (
                f" Não consegui renomear a pasta, mas os arquivos estão salvos: {exc}"
            )
            save_result_state(result)
    return result


def revise_lyrics(result: dict, rows: list[dict]) -> None:
    """Atualiza os arquivos e o ZIP sem repetir separação ou transcrição."""
    midi_dir = Path(result["midi_dir"])
    backup = Path(result["result_dir"]) / "midi_originais"
    original = backup / "completo.mid"
    if original.exists():
        events = export_karaoke(original, rows, backup)
        apply_mix(result["result_dir"], result.get("mix_settings"))
    else:
        events = export_karaoke(midi_dir / "completo.mid", rows, midi_dir)
    Path(result["events_path"]).write_text(
        json.dumps(
            [{"seconds": t, "text": "[QUEBRA]" if text == "\r\n" else text} for t, text in events],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    text = "".join(part for _, part in events)
    lyrics_txt = Path(result["result_dir"]) / "letra_reconhecida.txt"
    lyrics_txt.write_text(text, encoding="utf-8")
    _zip_results(
        Path(result["job_dir"]),
        Path(result["result_dir"]),
        result["include_stems"],
        Path(result["stems_dir"]) if Path(result["stems_dir"]).is_dir() else None,
    )
    result.update(
        karaoke_mid=str(midi_dir / "karaoke.mid"),
        karaoke_kar=str(midi_dir / "karaoke.kar"),
        lyrics_txt=str(lyrics_txt),
    )

    save_result_state(result)


def remix_result(result: dict, settings: dict) -> None:
    """Ajusta os MIDIs já gerados sem executar modelos de áudio novamente."""
    result["mix_settings"] = apply_mix(result["result_dir"], settings)
    if result.get("render_samples"):
        from sample_audio import render_result
        render_result(result["result_dir"])
    _zip_results(
        Path(result["job_dir"]),
        Path(result["result_dir"]),
        result.get("include_stems", False),
        Path(result["stems_dir"]) if Path(result["stems_dir"]).is_dir() else None,
    )
    save_result_state(result)
