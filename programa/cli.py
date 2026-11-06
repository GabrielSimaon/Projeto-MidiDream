"""Mesma pipeline da interface, para automatizar conversões pelo terminal."""

import argparse
import json
from pathlib import Path
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="Áudio → MIDI multifaixa e karaokê")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--audio", type=Path, help="Áudio local ou primeiro stem")
    parser.add_argument("--stems", type=Path, help="Pasta com stems já separados")
    parser.add_argument("-o", "--output", type=Path, help="Pasta NOVA de saída")
    parser.add_argument("--bpm", type=float, required=True, help="BPM informado, entre 30 e 300")
    parser.add_argument("--voice", choices=["choir", "flute"], default="flute")
    parser.add_argument("--language", default="pt", help="Código Whisper, ex.: pt, en, es, auto")
    parser.add_argument(
        "--lyrics", type=Path, required=True, help="Letra UTF-8 com cifras, com ou sem colchetes"
    )
    parser.add_argument("--timed-lyrics", type=Path, help="JSON de sílabas com tempos em segundos")
    parser.add_argument("--no-lyrics", action="store_true")
    parser.add_argument("--include-stems", action="store_true")
    parser.add_argument("--no-samples", action="store_true", help="Gerar somente MIDI/KAR")
    args = parser.parse_args()
    output = args.output or Path("trabalhos") / datetime.now().strftime("job_%Y%m%d_%H%M%S_%f")
    if output.exists() and any(output.iterdir()):
        parser.error("Escolha uma pasta vazia para evitar misturar resultados anteriores.")
    try:
        from pipeline import run_pipeline
        from quality import fixed_settings

        result = run_pipeline(
            output,
            require_guide=True,
            render_samples=not args.no_samples,
            audio_path=args.audio,
            uploaded_stems_dir=args.stems,
            bpm_override=args.bpm,
            language=args.language,
            manual_lyrics=args.lyrics.read_text(encoding="utf-8-sig") if args.lyrics else "",
            timed_lyrics=json.loads(args.timed_lyrics.read_text(encoding="utf-8-sig"))
            if args.timed_lyrics
            else None,
            include_stems_in_zip=args.include_stems,
            progress=lambda stage, detail: print(f"[{stage}] {detail}", flush=True),
            **{
                **fixed_settings(
                    stems_provided=bool(args.stems),
                    vocal_program=52 if args.voice == "choir" else 73,
                ),
                "recognize_lyrics": not args.no_lyrics,
            },
        )
        print("ZIP:", result["archive"])
        if result["transcription_warning"]:
            print("AVISO:", result["transcription_warning"])
        return 0
    except Exception as exc:
        print(f"ERRO: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
