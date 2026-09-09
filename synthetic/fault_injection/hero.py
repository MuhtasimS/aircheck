"""Create the deliberate M2 hero faults without changing clean originals."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess


HERO_MASTER_FILENAME = "the-last-lightkeeper-delivery-v1.mov"
HERO_CAPTIONS_FILENAME = "the-last-lightkeeper-delivery-v1.srt"


@dataclass(frozen=True)
class HeroFaultFiles:
    """Paths written by the bounded fixture-only fault injector."""

    master: Path
    captions: Path


def inject_hero_faults(
    *,
    clean_master: Path,
    destination: Path,
) -> HeroFaultFiles:
    """Write a copy with the four declared M2 defects into a new directory.

    The clean source is only read. This is fixture construction, never a
    runtime remediation or an authorization-controlled product action.
    """

    if not clean_master.is_file():
        raise FileNotFoundError(clean_master)
    if destination.exists():
        raise FileExistsError(destination)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("M2 requires ffmpeg for deterministic fixture injection")

    destination.mkdir(parents=True)
    master = destination / HERO_MASTER_FILENAME
    captions = destination / HERO_CAPTIONS_FILENAME
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(clean_master),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "copy",
            "-af",
            "volume=5dB",
            "-c:a",
            "pcm_s16le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-metadata",
            "creation_time=1970-01-01T00:00:00Z",
            "-movflags",
            "+faststart",
            str(master),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"M2 hero fault injection failed: {completed.stderr.strip()}")
    captions.write_text(
        "1\n00:00:00,000 --> 00:00:00,500\nThe last lightkeeper.\n\n"
        "2\n00:00:00,500 --> 00:00:01,000\nA Northstar preview.\n",
        encoding="utf-8",
    )
    return HeroFaultFiles(master=master, captions=captions)
