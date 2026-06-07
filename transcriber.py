"""
Speech-to-text transcription client.

Sends a WAV file to the transcription endpoint and returns the text.
"""

import os
import time
import hashlib
from pathlib import Path
from typing import Optional

import requests

import website_client

TRANSCRIPTION_URL = "https://api.mistral.ai/v1/audio/transcriptions"
DEFAULT_MODEL = "voxtral-mini-transcribe-2602"
TIMEOUT_SEC = 60


class TranscriptionError(Exception):
    """Raised when the transcription API returns an error."""


class TranscriptionClient:
    """Thin wrapper around the audio transcription REST API."""

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        license_token: str = "",
        device_id: str = "",
    ) -> None:
        self.license_token = (license_token or "").strip()
        self.device_id = (device_id or "").strip()
        self.last_usage: dict = {}

        if not self.license_token and not api_key:
            raise TranscriptionError("No transcription credentials available. Activate license and retry.")
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

        if self.license_token and self.device_id:
            try:
                proxy_result = website_client.transcribe_via_proxy(
                    wav_path=wav_path,
                    token=self.license_token,
                    device_id=self.device_id,
                    model=self.model,
                    language=language,
                    prompt=prompt,
                    idempotency_key=hashlib.sha256(
                        f"{self.device_id}:{Path(wav_path).name}:{time.time_ns()}".encode("utf-8")
                    ).hexdigest(),
                )
                self.last_usage = {
                    "remainingChars": proxy_result.usage.remaining_chars,
                    "usedChars": proxy_result.usage.used_chars,
                    "usedWords": proxy_result.usage.used_words,
                    "quotaChars": proxy_result.usage.quota_chars,
                }
                return proxy_result.text.strip()
            except website_client.WebsiteAPIError as exc:
                raise TranscriptionError(str(exc)) from exc

        headers = {"Authorization": f"Bearer {self.api_key}"}

        data: dict = {"model": self.model}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt

        with open(wav_path, "rb") as audio_file:
            files = {"file": (Path(wav_path).name, audio_file, "audio/wav")}
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
