#!/usr/bin/env python3
"""Convenience script for cloning a voice and synthesising the word "Michele".

This script wires together the :class:`BaseSpeakerTTS` and
:class:`ToneColorConverter` APIs that ship with OpenVoice so that users can
provide a short ``.mp3`` (or ``.wav``) reference clip and instantly hear the
cloned pronunciation of ``"Michele"``.  The implementation mirrors the logic
used by the interactive Gradio demo but runs fully from the command line to
support quick testing or automation.

Example
-------
Assuming the checkpoints from the official documentation have been extracted
into the ``checkpoints`` directory, clone a voice with:

```
python scripts/cross_lingual_voice_clone_demo.py /path/to/reference.mp3
```

The synthesised audio will be saved to ``outputs/michele_clone.wav`` by
default.  Additional options are available via ``--help``.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Dict

import torch

from openvoice.api import BaseSpeakerTTS, ToneColorConverter


# Base speaker metadata for the lightweight demo models that ship with the
# project.  The entries map the human readable language name to the location of
# the corresponding base-speaker checkpoint and embedding files.  Only the
# languages that are bundled with the public checkpoint release are listed
# here.  Additional languages can be added by extending this structure.
BASE_SPEAKER_CONFIGS: Dict[str, Dict[str, object]] = {
    "english": {
        "checkpoint_dir": Path("checkpoints/base_speakers/EN"),
        "style_embeddings": {
            "default": "en_default_se.pth",
            # All non-default styles re-use the same embedding in the public
            # demo checkpoints.
            "whispering": "en_style_se.pth",
            "shouting": "en_style_se.pth",
            "excited": "en_style_se.pth",
            "cheerful": "en_style_se.pth",
            "terrified": "en_style_se.pth",
            "angry": "en_style_se.pth",
            "sad": "en_style_se.pth",
            "friendly": "en_style_se.pth",
        },
        "friendly_name": "English",
    },
    "chinese": {
        "checkpoint_dir": Path("checkpoints/base_speakers/ZH"),
        "style_embeddings": {
            "default": "zh_default_se.pth",
        },
        "friendly_name": "Chinese",
    },
}


def _friendly_path(path: Path) -> str:
    """Return a readable version of ``path`` for error messages."""

    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _validate_checkpoint_files(config: Dict[str, object]) -> None:
    """Ensure the expected checkpoint artefacts exist.

    Users frequently forget to download the demo checkpoints prior to running a
    script.  Raising an informative ``FileNotFoundError`` gives a better error
    than the default ``RuntimeError`` thrown by :func:`torch.load`.
    """

    checkpoint_dir = config["checkpoint_dir"]
    config_path = checkpoint_dir / "config.json"
    checkpoint_path = checkpoint_dir / "checkpoint.pth"

    if not config_path.is_file() or not checkpoint_path.is_file():
        raise FileNotFoundError(
            "The base speaker checkpoint for this language is missing. "
            "Download the demo checkpoints described in docs/USAGE.md and "
            "extract them so that the files "
            f"'{_friendly_path(config_path)}' and "
            f"'{_friendly_path(checkpoint_path)}' exist."
        )

    for embedding_name in config["style_embeddings"].values():
        embedding_path = checkpoint_dir / embedding_name
        if not embedding_path.is_file():
            raise FileNotFoundError(
                "Could not find the speaker embedding file '"
                f"{_friendly_path(embedding_path)}'."
            )


def _resolve_device(user_choice: str | None) -> str:
    if user_choice:
        return user_choice
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_base_speaker(
    language: str, style: str, device: str
) -> tuple[BaseSpeakerTTS, torch.Tensor, str]:
    """Instantiate the TTS model and fetch the source speaker embedding."""

    language_key = language.lower()
    if language_key not in BASE_SPEAKER_CONFIGS:
        supported = ", ".join(sorted(BASE_SPEAKER_CONFIGS))
        raise ValueError(
            f"Unsupported language '{language}'. Available options: {supported}."
        )

    config = BASE_SPEAKER_CONFIGS[language_key]
    _validate_checkpoint_files(config)

    style_embeddings = config["style_embeddings"]
    if style not in style_embeddings:
        supported_styles = ", ".join(style_embeddings)
        raise ValueError(
            f"Style '{style}' is not available for {config['friendly_name']}. "
            f"Supported styles: {supported_styles}."
        )

    checkpoint_dir: Path = config["checkpoint_dir"]
    base_speaker = BaseSpeakerTTS(
        str(checkpoint_dir / "config.json"), device=device
    )
    base_speaker.load_ckpt(str(checkpoint_dir / "checkpoint.pth"))

    source_embedding_path = checkpoint_dir / style_embeddings[style]
    source_embedding = torch.load(source_embedding_path, map_location=device).to(
        device
    )

    friendly_language = config["friendly_name"]
    return base_speaker, source_embedding, friendly_language


def _load_tone_color_converter(device: str, enable_watermark: bool) -> ToneColorConverter:
    converter_dir = Path("checkpoints/converter")
    config_path = converter_dir / "config.json"
    checkpoint_path = converter_dir / "checkpoint.pth"

    if not config_path.is_file() or not checkpoint_path.is_file():
        raise FileNotFoundError(
            "The tone color converter checkpoint is missing. Download and "
            "extract the demo checkpoints as described in docs/USAGE.md so "
            f"that '{_friendly_path(config_path)}' and "
            f"'{_friendly_path(checkpoint_path)}' are present."
        )

    converter = ToneColorConverter(
        str(config_path), device=device, enable_watermark=enable_watermark
    )
    converter.load_ckpt(str(checkpoint_path))
    return converter


def clone_voice(
    reference_audio: Path,
    *,
    output_path: Path,
    text: str,
    language: str,
    style: str,
    device: str,
    tau: float,
    watermark_message: str,
    enable_watermark: bool,
) -> Path:
    """Clone the reference audio and return the output path."""

    if not reference_audio.is_file():
        raise FileNotFoundError(f"Reference audio not found: {reference_audio}")

    base_tts, source_se, friendly_language = _load_base_speaker(language, style, device)
    converter = _load_tone_color_converter(device, enable_watermark)

    target_se = converter.extract_se(str(reference_audio))

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="openvoice_demo_") as workdir:
        intermediate_tts = Path(workdir) / "tts.wav"
        base_tts.tts(
            text,
            str(intermediate_tts),
            speaker=style,
            language=friendly_language,
        )

        converter.convert(
            audio_src_path=str(intermediate_tts),
            src_se=source_se,
            tgt_se=target_se,
            output_path=str(output_path),
            tau=tau,
            message=watermark_message,
        )

    return output_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clone the tone colour of a short reference clip and synthesise the "
            'word "Michele" using the OpenVoice demo checkpoints.'
        )
    )
    parser.add_argument(
        "reference_audio",
        type=Path,
        help="Path to the reference .mp3 or .wav file whose voice should be cloned.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/michele_clone.wav"),
        help="Destination for the generated waveform.",
    )
    parser.add_argument(
        "--text",
        default="Michele",
        help=(
            "Text to be spoken by the cloned voice. Defaults to 'Michele' as "
            "requested by the demo."),
    )
    parser.add_argument(
        "--language",
        default="english",
        help="Base speaker language to use (english or chinese).",
    )
    parser.add_argument(
        "--style",
        default="default",
        help=(
            "Voice style for the base speaker. English supports default, "
            "whispering, shouting, excited, cheerful, terrified, angry, sad "
            "and friendly. Chinese supports only default."
        ),
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Computation device (e.g. cuda:0). Defaults to CUDA when available, otherwise CPU.",
    )
    parser.add_argument(
        "--tau",
        type=float,
        default=0.3,
        help="Temperature parameter controlling the strength of colour conversion.",
    )
    parser.add_argument(
        "--watermark-message",
        default="@MyShell",
        help="Message embedded into the output watermark (ignored when watermarking is disabled).",
    )
    parser.add_argument(
        "--disable-watermark",
        action="store_true",
        help="Disable audio watermarking when supported by the checkpoint.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    device = _resolve_device(args.device)

    try:
        output_path = clone_voice(
            args.reference_audio,
            output_path=args.output,
            text=args.text,
            language=args.language,
            style=args.style,
            device=device,
            tau=args.tau,
            watermark_message=args.watermark_message,
            enable_watermark=not args.disable_watermark,
        )
    except Exception as exc:  # pragma: no cover - convenience for CLI usage
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[SUCCESS] Generated cloned speech at {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
