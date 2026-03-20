"""
Generate the pre-recorded greeting audio file using OpenAI TTS.

Usage:
    python generate_greeting.py

Creates assets/greeting.wav (24kHz, mono, 16-bit PCM).
Requires OPENAI_API_KEY in .env.local (or environment).
"""

import asyncio
import io
import os
import wave
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")

ASSETS_DIR = Path(__file__).parent / "assets"
OUTPUT_PATH = ASSETS_DIR / "greeting.wav"

GREETING_TEXT = (
    "Hey Hello there! I'm your AI assistant from Autonomic. "
    "We help businesses automate customer interactions with intelligent AI agents. "
    "How can I help you today?"
)
VOICE = "alloy"
TARGET_SAMPLE_RATE = 24000
TARGET_CHANNELS = 1


async def generate_wav() -> bytes:
    """Generate WAV audio bytes using OpenAI TTS (same voice as the agent)."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI()

    print(f"Generating greeting with OpenAI TTS (voice: {VOICE})...")
    response = await client.audio.speech.create(
        model="tts-1",
        voice=VOICE,
        input=GREETING_TEXT,
        response_format="wav",
    )

    return response.content


def convert_wav_to_target_format(wav_bytes: bytes) -> bytes:
    """Re-encode WAV to 24kHz mono 16-bit PCM if needed."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as src:
        sr = src.getframerate()
        ch = src.getnchannels()
        sw = src.getsampwidth()

        if sr == TARGET_SAMPLE_RATE and ch == TARGET_CHANNELS and sw == 2:
            return wav_bytes

    # Need resampling — try miniaudio, then ffmpeg
    try:
        import miniaudio

        decoded = miniaudio.decode(
            wav_bytes,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=TARGET_CHANNELS,
            sample_rate=TARGET_SAMPLE_RATE,
        )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(TARGET_CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(TARGET_SAMPLE_RATE)
            wf.writeframes(decoded.samples)
        return buf.getvalue()
    except ImportError:
        pass

    try:
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_in:
            tmp_in.write(wav_bytes)
            tmp_in_path = tmp_in.name

        tmp_out_path = tmp_in_path + ".out.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", tmp_in_path,
                "-ar", str(TARGET_SAMPLE_RATE),
                "-ac", str(TARGET_CHANNELS),
                "-sample_fmt", "s16",
                tmp_out_path,
            ],
            capture_output=True,
            check=True,
        )
        result = Path(tmp_out_path).read_bytes()
        os.unlink(tmp_in_path)
        os.unlink(tmp_out_path)
        return result
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    print("⚠️  Could not resample — saving OpenAI output as-is (should still work)")
    return wav_bytes


async def main():
    ASSETS_DIR.mkdir(exist_ok=True)

    wav_bytes = await generate_wav()
    if not wav_bytes:
        print("No audio data received from OpenAI TTS")
        return

    print(f"Got {len(wav_bytes)} bytes of WAV audio")

    final = convert_wav_to_target_format(wav_bytes)
    OUTPUT_PATH.write_bytes(final)
    print(f"✅ Greeting saved to {OUTPUT_PATH} (voice: {VOICE})")


if __name__ == "__main__":
    asyncio.run(main())
    print("\nDone!")
