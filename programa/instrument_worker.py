"""Reconhece presença com AST e extrai apenas candidatos com AudioSep, em CPU."""
import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

from instrument_detection import TARGETS, decide_presence, verify_identity, duplicate_source

VENDOR = Path(__file__).resolve().parent / "vendor" / "audiosep"
WEIGHTS = {
    "audiosep_base_4M_steps.ckpt": "f8cda01bfd0ebd141eef45d41db7a3ada23a56568465840d3cff04b8010ce82c",
    "music_speech_audioset_epoch_15_esc_89.98.pt": "51c68f12f9d7ea25fdaaccf741ec7f81e93ee594455410f3bca4f47f88d8e006",
}


def announce(message):
    print("INSTRUMENT " + message, flush=True)


def load_backend():
    sys.path.insert(0, str(VENDOR))
    spec = importlib.util.spec_from_file_location("audiosep_backend", VENDOR / "pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def detect(audio, sr):
    import librosa
    import numpy as np
    import torch
    from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
    model_id = "MIT/ast-finetuned-audioset-10-10-0.4593"
    announce("Reconhecendo os instrumentos presentes na música…")
    extractor = AutoFeatureExtractor.from_pretrained(model_id)
    model = AutoModelForAudioClassification.from_pretrained(model_id).eval()
    audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    # Janelas de 6 s com avanço de 4 s — menos janelas, mesma cobertura.
    windows, size, hop = [], 96000, 64000
    for offset in range(0, len(audio), hop):
        segment = audio[offset:offset + size]
        if len(segment) < 1600 or np.max(np.abs(segment), initial=0) < 1e-5:
            continue
        inputs = extractor(segment, sampling_rate=16000, return_tensors="pt")
        with torch.inference_mode():
            scores = model(**inputs).logits.sigmoid()[0].cpu().numpy()
        windows.append({model.config.id2label[i]: float(score) for i, score in enumerate(scores)})
        announce(f"Reconhecendo: {min(len(audio), offset + size) / len(audio):.0%}")
    del model, extractor
    gc.collect()
    return decide_presence(windows)


def download_weights(root):
    import urllib.request
    import time
    folder = root / "checkpoint"
    folder.mkdir(parents=True, exist_ok=True)
    for name, expected in WEIGHTS.items():
        target = folder / name
        marker = folder / (name + ".sha256")
        if target.is_file() and marker.is_file() and marker.read_text() == f"{target.stat().st_size}:{target.stat().st_mtime_ns}:{expected}":
            continue
        announce("Baixando e conferindo o modelo de separação: " + name)
        partial = target.with_suffix(target.suffix + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request("https://huggingface.co/spaces/Audio-AGI/AudioSep/resolve/main/checkpoint/" + name,
            headers={"Range": f"bytes={offset}-"} if offset else {})
        with urllib.request.urlopen(request, timeout=60) as response:
            resume = offset > 0 and response.status == 206 and response.headers.get("Content-Range", "").startswith(f"bytes {offset}-")
            remaining = int(response.headers.get("Content-Length") or 0)
            required = remaining + 512 * 1024**2
            if shutil.disk_usage(folder).free < required:
                raise OSError(f"Espaço insuficiente em {folder}: o download exige {required / 1024**3:.1f} GB livres. Escolha outro disco em Pasta dos modelos e cache.")
            size, last = offset if resume else 0, 0.0
            with partial.open("ab" if resume else "wb") as stream:
                while block := response.read(4 * 1024 * 1024):
                    stream.write(block)
                    size += len(block)
                    if time.monotonic() - last >= 3:
                        announce(f"Baixando modelo: {size / (1024 * 1024):.0f} MB recebidos…")
                        last = time.monotonic()
        digest = hashlib.sha256()
        with partial.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            partial.unlink(missing_ok=True)
            raise RuntimeError("O download do modelo falhou na verificação. Tente novamente.")
        partial.replace(target)
        marker.write_text(f"{target.stat().st_size}:{target.stat().st_mtime_ns}:{expected}")


def extract(model, audio, description):
    import numpy as np
    import torch
    # Inferência com sobreposição, sem remover silêncios nem deslocar o começo.
    # Janela de 6 s com avanço de 4 s: menos sobreposição, menos processamento.
    length, hop = 32000 * 6, 32000 * 4
    result, weight = np.zeros(len(audio), dtype=np.float32), np.zeros(len(audio), dtype=np.float32)
    with torch.inference_mode():
        condition = model.query_encoder.get_query_embed(modality="text", text=[description], device="cpu")
        for start in range(0, len(audio), hop):
            stop = min(len(audio), start + length)
            chunk = audio[start:stop]
            padded = np.pad(chunk, (0, max(0, length - len(chunk))))
            data = {"mixture": torch.from_numpy(padded)[None, None, :], "condition": condition}
            separated = model.ss_model(data)["waveform"].squeeze().cpu().numpy()[:len(chunk)]
            window = np.ones(len(chunk), dtype=np.float32)
            fade = min(32000, len(chunk))
            if start:
                window[:fade] *= np.linspace(0.001, 1, fade)
            if stop < len(audio):
                window[-fade:] *= np.linspace(1, 0.001, fade)
            result[start:stop] += separated * window
            weight[start:stop] += window
    return result / np.maximum(weight, 1e-6)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify-known", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--models", type=Path)
    args = parser.parse_args()
    if args.check:
        import torch, torchaudio, torchvision, torchlibrosa
        from transformers import AutoModelForAudioClassification
        load_backend()
        return
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    # Usa mais threads para inferência de classificação; separação é limitada pela memória.
    available = os.cpu_count() or 1
    torch.set_num_threads(min(available, 6))
    source, output, root = args.source, args.output, args.models
    output.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    # Só o acompanhamento entra no segundo separador, sem voz, baixo ou bateria.
    files = [p for p in source.iterdir() if p.suffix.lower() in (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".webm")]
    from instrument_catalog import classify_name
    accompaniment, copied, preserved = [], [], []
    for file in files:
        kind = classify_name(file.name)
        if kind in ("vocals", "bass", "drums"):
            shutil.copy2(file, output / file.name)
            copied.append(file.name)
        elif kind in ("piano", "guitar", "accordion", "acoustic_guitar", "electric_guitar"):
            # Uma fonte já separada pelo Demucs ou fornecida pelo usuário é sempre preservada em sua faixa própria
            y, _ = librosa.load(str(file), sr=32000, mono=True)
            sf.write(output / (kind + ".wav"), y, 32000, subtype="FLOAT")
            preserved.append(kind)
        elif kind != "metronome":
            accompaniment.append(file)
    audio = np.zeros(0, dtype=np.float32)
    for file in accompaniment:
        y, _ = librosa.load(str(file), sr=32000, mono=True)
        if len(y) > len(audio):
            audio = np.pad(audio, (0, len(y) - len(audio)))
        audio[:len(y)] += y
    errors = []
    try:
        presence = detect(audio, 32000) if len(audio) and np.max(np.abs(audio), initial=0) > 1e-6 else decide_presence([])
    except Exception as exc:
        errors.append("Reconhecimento: " + str(exc)[-400:])
        presence = decide_presence([])
        announce("Reconhecimento indisponível; preservando o acompanhamento original.")
    for kind in preserved:
        if kind in TARGETS:
            presence[kind] = {"label": TARGETS[kind]["label"], "score": None, "windows": 0,
                              "present": True, "source": "preserved"}
    candidates = sorted((kind for kind, data in presence.items() if data["present"] and kind not in preserved),
                        key=lambda kind: presence[kind].get("score") or 0, reverse=True)
    exported, residual = list(preserved), audio.copy()
    validation = {}
    accepted_sources = {}
    model = None
    if candidates:
        try:
            download_weights(root)
            os.chdir(root)
            backend = load_backend()
            announce("Carregando o modelo de separação por instrumento…")
            model = backend.build_audiosep(str(VENDOR / "config/audiosep_base.yaml"),
                                          str(root / "checkpoint/audiosep_base_4M_steps.ckpt"), torch.device("cpu"))
        except Exception as exc:
            errors.append("Separador: " + str(exc)[-400:])
            announce("Separador indisponível; preservando o acompanhamento para revisão.")
    if model is not None:
        reference = float(np.sqrt(np.mean(audio ** 2)))
        for kind in candidates:
            announce("Separando " + TARGETS[kind]["label"] + "…")
            try:
                separated = extract(model, audio, TARGETS[kind]["prompt"])
            except Exception as exc:
                errors.append(TARGETS[kind]["label"] + ": " + str(exc)[-400:])
                continue
            rms = float(np.sqrt(np.mean(separated ** 2)))
            if not np.isfinite(separated).all() or rms < max(1e-6, reference * 0.005):
                presence[kind]["rejected"] = "energia insuficiente"
                continue
            duplicate = duplicate_source(separated, accepted_sources)
            if duplicate:
                presence[kind]["rejected"] = "áudio duplicado de " + duplicate
                validation[kind] = {"accepted": False, "duplicate_of": duplicate}
                continue
            try:
                announce("Conferindo o timbre extraído: " + TARGETS[kind]["label"] + "…")
                validation[kind] = verify_identity(kind, detect(separated, 32000))
                if not validation[kind]["accepted"]:
                    presence[kind]["rejected"] = "conflito de timbre com " + validation[kind]["stronger_instrument"]
                    continue
            except Exception as exc:
                validation[kind] = {"accepted": True, "uncertain": True, "error": str(exc)[-300:]}
                errors.append("Conferência de " + TARGETS[kind]["label"] + ": " + str(exc)[-300:])
            sf.write(output / (kind + ".wav"), separated, 32000, subtype="FLOAT")
            residual -= separated
            accepted_sources[kind] = separated
            exported.append(kind)
    # Incerteza de classificação não significa ausência de acompanhamento.
    reference = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    residual_rms = float(np.sqrt(np.mean(residual ** 2))) if len(residual) else 0.0
    fallback = residual_rms > max(1e-6, reference * 0.06)
    if fallback:
        sf.write(output / "other.wav", residual, 32000, subtype="FLOAT")
        exported.append("other")
        announce("Acompanhamento incerto preservado para transcrição e revisão.")
    # O resíduo fica fora da pasta transcrita, evitando duplicar os instrumentos.
    leftover = output.parent / "acompanhamento_nao_identificado.wav"
    sf.write(leftover, residual if len(residual) else np.zeros(1, dtype=np.float32), 32000, subtype="FLOAT")
    data = {"detector": "AST/AudioSet", "separator": "AudioSep", "presence": presence,
            "validation": validation, "automatic_stems_rechecked": args.verify_known,
            "exported": exported, "preserved": preserved, "fallback": fallback, "errors": errors, "residual": leftover.name,
            "notice": "Faixas já separadas são preservadas. Acompanhamento incerto permanece em other.mid para revisão, sem atribuição automática de um instrumento específico."}
    (output / "instrumentos.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    announce("Separação concluída: " + (", ".join(TARGETS.get(k, {"label": {"guitar": "Guitarra / violão", "other": "Acompanhamento não identificado"}.get(k, k)})["label"] for k in exported) or "nenhum instrumento com confiança suficiente"))


if __name__ == "__main__":
    main()
