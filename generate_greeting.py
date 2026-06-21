"""
Generate the pre-recorded greeting audio file using Cartesia TTS.

Usage:
    python generate_greeting.py

Creates assets/greeting.wav (24kHz, mono, 16-bit PCM).
Requires CARTESIA_API_KEY in .env.local (or environment).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from cartesia import Cartesia

load_dotenv(dotenv_path=".env.local")

ASSETS_DIR = Path(__file__).parent / "assets"
OUTPUT_PATH = ASSETS_DIR / "greeting.wav"

# Must match the voice / model used in agent.py
VOICE_ID = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
MODEL = "sonic-3"
TARGET_SAMPLE_RATE = 24_000  # Hz, mono, 16-bit PCM

GREETING_TEXT = (
    "Hey, hello there! I'm your AI assistant from Autonomiq. "
    "We help businesses automate customer interactions with intelligent AI agents. "
    "How can I help you today?"
)


def main() -> None:
    api_key = os.environ.get("CARTESIA_API_KEY")
    if not api_key:
        raise ValueError(
            "CARTESIA_API_KEY is not set in .env.local or environment"
        )

    ASSETS_DIR.mkdir(exist_ok=True)

    client = Cartesia(api_key=api_key)

    print(
        f"Generating greeting with Cartesia TTS (model: {MODEL}, voice: {VOICE_ID})...")
    response = client.tts.generate(
        model_id=MODEL,
        transcript=GREETING_TEXT,
        voice={"mode": "id", "id": VOICE_ID},
        output_format={
            "container": "wav",
            "encoding": "pcm_s16le",
            "sample_rate": TARGET_SAMPLE_RATE,
        },
    )
    data = response.read()

    OUTPUT_PATH.write_bytes(data)
    print(
        f"✅ Greeting saved to {OUTPUT_PATH} "
        f"({len(data)} bytes, {TARGET_SAMPLE_RATE}Hz mono s16le)"
    )


if __name__ == "__main__":
    main()
