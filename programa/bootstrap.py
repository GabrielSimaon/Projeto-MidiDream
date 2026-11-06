"""Instalação privada e inicialização do aplicativo. Executado pelo iniciador nativo."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

SOURCE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def announce(message):
    print(message, flush=True)


def fetch(url, target):
    """Baixa para arquivo temporário; interrupções não deixam binários incompletos."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "YouTubeMidiStudio/2.0"})
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            temporary.open("wb") as output,
        ):
            total = int(response.headers.get("Content-Length") or 0)
            received, last = 0, time.monotonic()
            while block := response.read(1024 * 1024):
                output.write(block)
                received += len(block)
                if time.monotonic() - last > 5:
                    suffix = f" de {total // 1048576} MB" if total else " MB"
                    announce(f"  Baixando: {received // 1048576}{suffix}")
                    last = time.monotonic()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def read_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "YouTubeMidiStudio/2.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def verify_sha256(path, expected):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest().lower() != expected.strip().lower():
        Path(path).unlink(missing_ok=True)
        raise RuntimeError(
            "Um download chegou incompleto ou foi alterado. Clique em Tentar novamente."
        )


def extract_binaries(archive, folder, names):
    """Extrai apenas executáveis conhecidos, sem aceitar caminhos do ZIP."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for name in names:
            matches = [
                item
                for item in zipped.infolist()
                if not item.is_dir() and Path(item.filename).name == name
            ]
            if len(matches) != 1:
                raise RuntimeError(f"O download não contém exatamente um {name}. Tente novamente.")
            target = folder / name
            temporary = target.with_suffix(".part")
            with zipped.open(matches[0]) as src, temporary.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            temporary.replace(target)


def prepare_tools(base, update_deno=False):
    tools = base / "ferramentas"
    tools.mkdir(exist_ok=True)
    if not all((tools / name).exists() for name in ("ffmpeg.exe", "ffprobe.exe")):
        announce("[3/5] Baixando FFmpeg para processar o áudio...")
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        archive = fetch(url, base / "ffmpeg.zip")
        request = urllib.request.Request(
            url + ".sha256", headers={"User-Agent": "YouTubeMidiStudio/2.0"}
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            checksum = response.read(4096).decode("ascii").strip().split()[0]
        if len(checksum) != 64:
            raise RuntimeError(
                "O servidor do FFmpeg não forneceu um checksum válido. Tente novamente."
            )
        verify_sha256(archive, checksum)
        extract_binaries(archive, tools, ("ffmpeg.exe", "ffprobe.exe"))
        # Mantém os avisos/licenças do distribuidor junto aos binários baixados.
        with zipfile.ZipFile(archive) as zipped:
            license_files = [
                i
                for i in zipped.infolist()
                if Path(i.filename).name.upper() in ("LICENSE", "LICENSE.TXT", "COPYING")
            ]
            for i, entry in enumerate(license_files):
                (tools / f"FFmpeg-LICENSE-{i}.txt").write_bytes(zipped.read(entry))
        archive.unlink()
    return tools


def ensure_visual_cpp(base):
    """Só instala o redistribuível oficial Microsoft se os DLLs estiverem ausentes."""
    if os.name != "nt":
        return
    try:
        ctypes.WinDLL("msvcp140.dll")
        ctypes.WinDLL("vcruntime140_1.dll")
        return
    except OSError:
        pass
    announce("Preparando componente Microsoft. O Windows pode pedir permissão para instalá-lo.")
    installer = fetch(
        "https://aka.ms/vs/17/release/vc_redist.x64.exe", base / "Microsoft-VisualCpp.exe"
    )
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS: aguarda o instalador terminar.
    info.lpVerb = "runas"
    info.lpFile = str(installer)
    info.lpParameters = "/install /passive /norestart"
    info.nShow = 1
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        raise RuntimeError(
            "O componente Microsoft é necessário. Clique em Tentar novamente e permita a instalação."
        )
    kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel.CloseHandle(info.hProcess)
    if code.value not in (0, 1638, 3010):
        raise RuntimeError(
            f"O instalador Microsoft não terminou (código {code.value}). Tente novamente."
        )
    installer.unlink(missing_ok=True)


def run_step(command, log, message):
    announce(message)
    # Nenhum shell é usado. A saída técnica vai para o diagnóstico do instalador.
    with Path(log).open("ab") as output:
        result = subprocess.run(
            [str(part) for part in command],
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    if result.returncode:
        with Path(log).open("rb") as src:
            src.seek(max(0, Path(log).stat().st_size - 2200))
            tail = src.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{message}\n{tail}\nA instalação pode ser retomada pelo botão Tentar novamente."
        )


def prepare_dependencies(base, uv):
    marker = base / "instalado.json"
    requirements = SOURCE / "requirements-windows.lock"
    if not requirements.exists():
        requirements = SOURCE / "requirements.txt"
    fingerprint = hashlib.sha256(b"desktop-2.0\n" + requirements.read_bytes()).hexdigest()
    try:
        if json.loads(marker.read_text())["requirements"] == fingerprint:
            for name in (
                "torch",
                "torchaudio",
                "demucs",
                "basic-pitch",
                "faster-whisper",
                "streamlit",
                "setuptools",
            ):
                version(name)
            return
    except (OSError, ValueError, KeyError, PackageNotFoundError):
        pass
    log = base / "instalacao-detalhes.txt"
    ensure_visual_cpp(base)
    run_step(
        [
            uv,
            "pip",
            "install",
            "--python",
            sys.executable,
            "torch==2.5.1",
            "torchaudio==2.5.1",
            "--index-url",
            "https://download.pytorch.org/whl/cpu",
        ],
        log,
        "[4/5] Instalando o motor de áudio. O download é grande; aguarde...",
    )
    run_step(
        [uv, "pip", "install", "--python", sys.executable, "-r", requirements],
        log,
        "[4/5] Instalando a interface e as bibliotecas de música...",
    )
    run_step(
        [uv, "pip", "check", "--python", sys.executable], log, "[5/5] Conferindo a instalação..."
    )
    run_step(
        [
            sys.executable,
            "-c",
            "import torch, torchaudio, librosa, mido, streamlit, demucs; import basic_pitch.inference; import faster_whisper",
        ],
        log,
        "[5/5] Testando os componentes de áudio...",
    )
    temporary = marker.with_suffix(".part")
    temporary.write_text(json.dumps({"requirements": fingerprint}), encoding="utf-8")
    temporary.replace(marker)


def serve(base):
    import urllib.request

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(SOURCE / "app.py"),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.fileWatcherType",
        "none",
    ]
    ready = base / "interface.url.txt"
    announce("Abrindo o aplicativo...")
    with (base / "interface-diagnostico.txt").open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=SOURCE,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
        try:
            deadline = time.monotonic() + 180
            # A URL local não passa por proxies do sistema nem por serviços externos.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        "A interface não iniciou. Veja interface-diagnostico.txt em Meus arquivos → pasta anterior."
                    )
                try:
                    with opener.open(url + "/_stcore/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.3)
            else:
                raise RuntimeError("A interface demorou para iniciar. Clique em Tentar novamente.")
            ready.write_text(url, encoding="utf-8")
            announce(
                "Pronto! A interface abre automaticamente no navegador. Mantenha esta janela aberta."
            )
            return process.wait()
        finally:
            ready.unlink(missing_ok=True)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    args = parser.parse_args()
    base = args.base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    os.environ["MIDI_STUDIO_WORK_DIR"] = str(base / "trabalhos")
    os.environ["MIDI_STUDIO_DESKTOP"] = "1"
    os.environ["MIDI_STUDIO_UV"] = str(args.uv.resolve())
    os.environ["HF_HOME"] = str(base / "modelos" / "huggingface")
    os.environ["TORCH_HOME"] = str(base / "modelos" / "torch")
    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
    from performance import cpu_threads

    threads = str(cpu_threads())
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "TF_NUM_INTRAOP_THREADS",
    ):
        os.environ.setdefault(name, threads)
    os.environ.setdefault("TF_NUM_INTEROP_THREADS", "1")
    os.environ["MIDI_STUDIO_HIDE_CONSOLE"] = "1"
    os.environ["PYTHONPATH"] = str(SOURCE)
    try:
        tools = prepare_tools(base)
        os.environ["MIDI_STUDIO_TOOLS"] = str(tools)
        os.environ["PATH"] = str(tools) + os.pathsep + os.environ.get("PATH", "")
        prepare_dependencies(base, args.uv)
        return serve(base)
    except Exception as exc:
        announce("\nNão foi possível concluir: " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
