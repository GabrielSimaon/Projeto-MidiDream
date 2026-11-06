"""Reconhecimento isolado: falhas nativas do ASR não derrubam o servidor da interface."""

import argparse
import json
from pathlib import Path
from lyrics import transcribe_words


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="base")
    parser.add_argument("--language", default="pt")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--align-text", type=Path)
    args = parser.parse_args()
    callback = lambda fraction: print(f"LYRICS_PROGRESS {fraction:.4f}", flush=True)
    if args.align_text:
        from lyric_alignment import align_words

        words = align_words(
            args.audio,
            args.align_text.read_text(encoding="utf-8"),
            model_size=args.model,
            language=args.language,
            progress=callback,
        )
    else:
        words = transcribe_words(
            args.audio,
            model_size=args.model,
            language=args.language,
            fast=args.fast,
            progress=callback,
        )
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    temporary.replace(args.output)
    print("LYRICS_PROGRESS 1.0000", flush=True)


if __name__ == "__main__":
    main()
