"""Voice service — STT via Whisper, TTS via OpenAI."""

import asyncio
import io
import logging

from openai import OpenAI

from . import config

log = logging.getLogger("conduit.voice")


def _get_client() -> OpenAI:
    """Get an OpenAI client using the configured API key."""
    return OpenAI(api_key=config.OPENAI_API_KEY)


async def transcribe(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """Transcribe audio bytes to text using Whisper.

    Args:
        audio_bytes: Raw audio data (OGG, WebM, MP3, WAV, etc.)
        filename: Filename hint for format detection.

    Returns:
        Transcribed text string.
    """
    def _transcribe():
        client = _get_client()
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename
        result = client.audio.transcriptions.create(
            model=config.VOICE_STT_MODEL,
            file=audio_file,
        )
        return result.text

    return await asyncio.to_thread(_transcribe)


async def speak(text: str) -> bytes:
    """Convert text to speech using OpenAI TTS.

    Args:
        text: Text to speak.

    Returns:
        OGG/Opus audio bytes.
    """
    def _speak():
        client = _get_client()
        response = client.audio.speech.create(
            model=config.VOICE_TTS_MODEL,
            voice=config.VOICE_TTS_VOICE,
            input=text,
            response_format="opus",
        )
        return response.content

    return await asyncio.to_thread(_speak)
