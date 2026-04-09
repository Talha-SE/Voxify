"""
Audio recorder — supports microphone (sounddevice) and
system/loopback audio (soundcard WASAPI loopback on Windows).

Usage:
    rec = Recorder(source="mic", sample_rate=16000)
    rec.start()
    ...
    wav_path = rec.stop()   # returns path to saved .wav temp file
"""

import io
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

import numpy as np
import scipy.io.wavfile as wav

# ── Microphone via sounddevice ──────────────────────────────────────────────
try:
    import sounddevice as sd
    _SD_OK = True
except ImportError:
    _SD_OK = False

# ── System / loopback via soundcard ────────────────────────────────────────
try:
    import soundcard as sc
    _SC_OK = True
except ImportError:
    _SC_OK = False


AudioSource = Literal["mic", "system"]
ReliabilityMode = Literal["balanced", "latency", "accuracy"]


def adaptive_trim_silence(
    audio: np.ndarray,
    sample_rate: int,
    mode: ReliabilityMode = "balanced",
) -> np.ndarray:
    """Trim leading/trailing silence using adaptive RMS thresholds."""
    if audio.size == 0:
        return audio

    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    frame_size = max(128, int(sample_rate * 0.02))
    if mono.size < frame_size * 4:
        return audio

    frame_count = mono.size // frame_size
    shaped = mono[: frame_count * frame_size].reshape(frame_count, frame_size)
    rms = np.sqrt(np.mean(np.square(shaped), axis=1))
    if rms.size == 0:
        return audio

    floor = float(np.percentile(rms, 25))
    multipliers = {"latency": 1.8, "balanced": 2.2, "accuracy": 2.8}
    threshold = max(0.0025, floor * multipliers.get(mode, 2.2))
    speech = rms > threshold
    speech_indices = np.where(speech)[0]
    if speech_indices.size == 0:
        return audio

    pre_pad_sec = {"latency": 0.10, "balanced": 0.18, "accuracy": 0.25}.get(mode, 0.18)
    post_pad_sec = {"latency": 0.22, "balanced": 0.35, "accuracy": 0.50}.get(mode, 0.35)
    pre_pad = int(pre_pad_sec * sample_rate)
    post_pad = int(post_pad_sec * sample_rate)

    start = max(0, int(speech_indices[0] * frame_size) - pre_pad)
    end = min(len(mono), int((speech_indices[-1] + 1) * frame_size) + post_pad)
    if end <= start:
        return audio

    if audio.ndim > 1:
        return audio[start:end, :]
    return audio[start:end]


class Recorder:
    """Thread-safe audio recorder for mic or system loopback."""

    def __init__(
        self,
        source: AudioSource = "mic",
        sample_rate: int = 16_000,
        channels: int = 1,
        silence_trim_enabled: bool = True,
        reliability_mode: ReliabilityMode = "balanced",
    ) -> None:
        self.source = source
        self.sample_rate = sample_rate
        self.channels = channels
        self.silence_trim_enabled = silence_trim_enabled
        self.reliability_mode = reliability_mode

        self._frames: list[np.ndarray] = []
        self._recording = False
        self._thread: threading.Thread | None = None

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin recording in a background thread."""
        if self._recording:
            return
        self._frames.clear()
        self._recording = True
        target = (
            self._record_mic if self.source == "mic" else self._record_system
        )
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def stop(self) -> str:
        """Stop recording and return path to a temp WAV file."""
        self._recording = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        if not self._frames:
            raise RuntimeError("No audio captured.")

        audio = np.concatenate(self._frames, axis=0)

        # Convert to mono if needed
        if audio.ndim > 1 and self.channels == 1:
            audio = audio.mean(axis=1)

        if self.silence_trim_enabled:
            trimmed = adaptive_trim_silence(audio, self.sample_rate, mode=self.reliability_mode)
            if isinstance(trimmed, np.ndarray) and trimmed.size > 0:
                audio = trimmed

        # Normalise & convert to int16
        peak = np.abs(audio).max()
        if peak > 0:
            audio = audio / peak
        audio_int16 = (audio * 32767).astype(np.int16)

        # Write to temp file
        tmp = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False, prefix="voxtral_"
        )
        wav.write(tmp.name, self.sample_rate, audio_int16)
        tmp.close()
        return tmp.name

    @property
    def is_recording(self) -> bool:
        return self._recording

    # ── Internal recording loops ────────────────────────────────────────────

    def _record_mic(self) -> None:
        if not _SD_OK:
            raise RuntimeError("sounddevice not installed.")

        chunk = max(256, int(self.sample_rate * 0.05))  # ~50 ms chunks

        def callback(indata, frames, time_info, status):
            if self._recording:
                self._frames.append(indata.copy())

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            blocksize=chunk,
            latency="low",
            callback=callback,
        ):
            while self._recording:
                time.sleep(0.05)

    def _record_system(self) -> None:
        if not _SC_OK:
            raise RuntimeError("soundcard not installed.")

        # Get default loopback device (what's playing on speakers)
        try:
            mic = sc.get_microphone(
                id=str(sc.default_speaker().name), include_loopback=True
            )
        except Exception:
            # Fallback: first loopback device available
            loopbacks = [
                m for m in sc.all_microphones(include_loopback=True)
                if "loopback" in m.name.lower() or "stereo mix" in m.name.lower()
            ]
            if not loopbacks:
                # Last resort: use default output as loopback
                mic = sc.get_microphone(
                    id=str(sc.all_speakers()[0].name), include_loopback=True
                )
            else:
                mic = loopbacks[0]

        chunk = max(256, int(self.sample_rate * 0.05))

        with mic.recorder(samplerate=self.sample_rate, channels=self.channels) as m:
            while self._recording:
                data = m.record(numframes=chunk)
                self._frames.append(data.copy())


def list_input_devices() -> list[dict]:
    """Return available input device names for the settings UI."""
    devices = []
    if _SD_OK:
        try:
            for d in sd.query_devices():
                if d["max_input_channels"] > 0:
                    devices.append({"name": d["name"], "source": "mic"})
        except Exception:
            pass
    if _SC_OK:
        try:
            for m in sc.all_microphones(include_loopback=True):
                devices.append({"name": m.name, "source": "system"})
        except Exception:
            pass
    return devices
