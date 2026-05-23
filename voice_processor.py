"""
Procesamiento de voz para Telegram usando Gemini API.

STT: Gemini 3.1 Flash Lite transcribe audio a texto.
TTS: Gemini 3.1 Flash TTS genera voz natural desde texto.
"""

from __future__ import annotations

import base64
import io
import wave
from pathlib import Path
from typing import Any

import config
from rich.console import Console

console = Console()


try:
    from google import genai
    from pydub import AudioSegment

    _GENAI_AVAILABLE = True
except Exception:
    _GENAI_AVAILABLE = False
    genai = None  # type: ignore
    AudioSegment = None  # type: ignore


class VoiceProcessor:
    def __init__(self) -> None:
        self.api_key = config.GOOGLE_API_KEY
        self._client: Any | None = None
        if not self.api_key:
            console.print("[yellow]⚠️ GOOGLE_API_KEY no configurado. Voz deshabilitada.[/yellow]")

    def _get_client(self) -> Any:
        if self._client is None and _GENAI_AVAILABLE and self.api_key:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        """
        Transcribe audio a texto usando Gemini Flash Lite.
        `audio_bytes` debe ser el contenido binario del archivo de audio.
        """
        client = self._get_client()
        if not client:
            raise RuntimeError("Gemini client no disponible")

        # Convertir a WAV si es necesario (Gemini prefiere WAV/MP3)
        audio_bytes = self._normalize_audio(audio_bytes, mime_type)

        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite-preview",
                contents=[
                    "Transcribe el siguiente audio al español. Solo devuelve la transcripción, sin comentarios adicionales.",
                    {
                        "mime_type": "audio/wav",
                        "data": audio_bytes,
                    },
                ],
            )
            return (response.text or "").strip()
        except Exception as e:
            console.print(f"[red]❌ Error STT: {e}[/red]")
            raise

    def _normalize_audio(self, audio_bytes: bytes, mime_type: str) -> bytes:
        """Convierte audio a WAV mono 16kHz para Gemini."""
        if not AudioSegment:
            return audio_bytes

        try:
            # Determinar formato desde mime_type
            fmt = "ogg"
            if "mp3" in mime_type:
                fmt = "mp3"
            elif "wav" in mime_type:
                fmt = "wav"
            elif "m4a" in mime_type:
                fmt = "mp4"

            seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)
            # Gemini funciona bien con 16kHz mono
            seg = seg.set_frame_rate(16000).set_channels(1)
            buf = io.BytesIO()
            seg.export(buf, format="wav")
            return buf.getvalue()
        except Exception as e:
            console.print(f"[yellow]⚠️ No se pudo normalizar audio: {e}. Usando original.[/yellow]")
            return audio_bytes

    async def synthesize(self, text: str) -> bytes:
        """
        Genera audio OGG/Opus desde texto usando Gemini Flash TTS.
        Retorna bytes listos para enviar como voice message de Telegram.
        """
        client = self._get_client()
        if not client:
            raise RuntimeError("Gemini client no disponible")

        # Truncar texto muy largo (Telegram voice messages ~20MB limite, ~4000 chars es seguro)
        if len(text) > 4000:
            text = text[:4000] + "\n\n[El mensaje fue truncado para la versión de voz.]"

        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-tts-preview",
                contents=text,
                config={
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice": "Kore",  # Voz en español disponible
                    },
                },
            )

            # Extraer audio PCM de la respuesta
            pcm_audio = None
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("audio/"):
                    pcm_audio = part.inline_data.data
                    break

            if pcm_audio is None:
                raise RuntimeError("No se recibió audio de Gemini TTS")

            # Convertir PCM a OGG/Opus para Telegram
            return self._pcm_to_opus(pcm_audio)

        except Exception as e:
            console.print(f"[red]❌ Error TTS: {e}[/red]")
            raise

    def _pcm_to_opus(self, pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
        """Convierte PCM raw a OGG/Opus (formato de Telegram voice messages)."""
        if not AudioSegment:
            raise RuntimeError("pydub no disponible para conversión de audio")

        # Wrap PCM en WAV header
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wav_file:
            wav_file.setnchannels(1)      # mono
            wav_file.setsampwidth(2)      # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)

        wav_buf.seek(0)
        audio_segment = AudioSegment.from_wav(wav_buf)

        # Exportar a OGG/Opus
        ogg_buf = io.BytesIO()
        audio_segment.export(ogg_buf, format="ogg", codec="libopus")
        ogg_buf.seek(0)
        return ogg_buf.read()
