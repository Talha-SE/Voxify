"""
Speech-to-text transcription client.

Sends a WAV file to the transcription endpoint and returns the text.
"""

import os
import time
from pathlib import Path
from typing import Optional

import requests

TRANSCRIPTION_URL = "https://api.mistral.ai/v1/audio/transcriptions"
DEFAULT_MODEL = "voxtral-mini-2507"
TIMEOUT_SEC = 60


class TranscriptionError(Exception):
    """Raised when the transcription API returns an error."""


class TranscriptionClient:
    """Thin wrapper around the audio transcription REST API."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise TranscriptionError(
                "API key is not available. Press Start so the app can fetch it from the website."
            )
        self.api_key = api_key
        self.model = model

    # ── Public ──────────────────────────────────────────────────────────────

    def transcribe(
        self,
        wav_path: str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """
        Transcribe *wav_path* and return the text.

        Parameters
        ----------
        wav_path : str
            Path to the WAV audio file.
        language : str, optional
            ISO-639-1 language code (e.g. "en"). Pass ``None`` for auto-detect.
        prompt : str, optional
            Optional context / vocabulary hint for the model.

        Returns
        -------
        str
            Transcribed text (stripped).
        """
        if not Path(wav_path).exists():
            raise TranscriptionError(f"Audio file not found: {wav_path}")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        data: dict = {"model": self.model}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt

        with open(wav_path, "rb") as audio_file:
            files = {
                "file": (Path(wav_path).name, audio_file, "audio/wav"),
            }
            response = requests.post(
                TRANSCRIPTION_URL,
                headers=headers,
                data=data,
                files=files,
                timeout=TIMEOUT_SEC,
            )

        if response.status_code == 200:
            result = response.json()
            # API returns {"text": "...", ...}
            text = result.get("text", "").strip()
            return text
        else:
            self._raise_api_error(response)

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _raise_api_error(response: requests.Response) -> None:
        try:
            detail = response.json()
            msg = detail.get("message") or detail.get("error") or str(detail)
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        raise TranscriptionError(f"API error [{response.status_code}]: {msg}")

    def validate_key(self) -> bool:
        """Ping the models endpoint to verify the API key quickly."""
        try:
            resp = requests.get(
                "https://api.mistral.ai/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False


def cleanup_temp(path: str) -> None:
    """Delete a temporary audio file after transcription."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
