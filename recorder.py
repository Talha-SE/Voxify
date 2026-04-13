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
import sys
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


def _system_capture_keywords() -> tuple[str, ...]:
    return (
        "loopback",
        "stereo mix",
        "monitor",
        "what u hear",
        "blackhole",
        "soundflower",
        "vb-cable",
        "virtual audio",
    )


def _score_system_device_name(name: str) -> int:
    label = (name or "").lower()
    score = 0
    for keyword in _system_capture_keywords():
        if keyword in label:
            score += 2
    if "default" in label:
        score += 1
    return score


def _system_audio_setup_hint() -> str:
    if sys.platform == "darwin":
        return (
            "No system-audio loopback device found on macOS. "
            "Install and route output through a virtual device like BlackHole, Loopback, "
            "or Soundflower, then select System source again."
        )
    if sys.platform.startswith("linux"):
        return (
            "No system-audio monitor source found on Linux. "
            "Enable a PulseAudio/PipeWire monitor source (usually '*monitor') and retry."
        )
    return "No system-audio loopback source found. Enable Stereo Mix/loopback and retry."


def _system_channel_candidates(preferred_channels: int) -> tuple[int, ...]:
    base = max(1, int(preferred_channels or 1))
    candidates: list[int] = [base]
    for fallback in (2, 1):
        if fallback not in candidates:
            candidates.append(fallback)
    return tuple(candidates)


def _resolve_system_microphone():
    """Resolve the best available loopback/system capture microphone for this OS."""
    if not _SC_OK:
        raise RuntimeError("soundcard not installed.")

    # Windows: default speaker loopback is usually reliable.
    default_speaker_name = ""
    if sys.platform.startswith("win"):
        try:
            default_speaker = sc.default_speaker()
            if default_speaker:
                default_speaker_name = str(default_speaker.name).lower()
                return sc.get_microphone(id=str(default_speaker.name), include_loopback=True)
        except Exception:
            pass

    try:
        microphones = list(sc.all_microphones(include_loopback=True))
    except Exception:
        microphones = []

    if microphones:
        if default_speaker_name:
            for microphone in microphones:
                mic_name = str(getattr(microphone, "name", "")).lower()
                if default_speaker_name and default_speaker_name in mic_name:
                    return microphone

        ranked = sorted(
            microphones,
            key=lambda m: _score_system_device_name(getattr(m, "name", "")),
            reverse=True,
        )
        best = ranked[0]
        if _score_system_device_name(getattr(best, "name", "")) > 0:
            return best

    # macOS/Linux may expose virtual loopback as a normal input device.
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        try:
            normal_mics = list(sc.all_microphones(include_loopback=False))
        except Exception:
            normal_mics = []
        if normal_mics:
            ranked = sorted(
                normal_mics,
                key=lambda m: _score_system_device_name(getattr(m, "name", "")),
                reverse=True,
            )
            best = ranked[0]
            if _score_system_device_name(getattr(best, "name", "")) > 0:
                return best

    if microphones:
        return microphones[0]

    try:
        speakers = list(sc.all_speakers())
        if speakers:
            return sc.get_microphone(id=str(speakers[0].name), include_loopback=True)
    except Exception:
        pass

    raise RuntimeError(_system_audio_setup_hint())


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
        self._thread_error: Exception | None = None

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin recording in a background thread."""
        if self._recording:
            return
        self._frames.clear()
        self._thread_error = None
        self._recording = True
        target = (
            self._record_mic if self.source == "mic" else self._record_system
        )

        def _runner() -> None:
            try:
                target()
            except Exception as exc:
                self._thread_error = exc
                self._recording = False

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()

        # Surface immediate startup failures to the caller so fallback logic can engage.
        time.sleep(0.12)
        if self._thread_error is not None and (not self._thread or not self._thread.is_alive()):
            error = self._thread_error
            self._thread = None
            self._recording = False
            raise RuntimeError(str(error)) from error

    def stop(self) -> str:
        """Stop recording and return path to a temp WAV file."""
        self._recording = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        if not self._frames:
            if self._thread_error is not None:
                raise RuntimeError(str(self._thread_error)) from self._thread_error
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

        mic = _resolve_system_microphone()

        chunk = max(256, int(self.sample_rate * 0.05))
        last_exc: Exception | None = None

        for channel_count in _system_channel_candidates(self.channels):
            try:
                with mic.recorder(samplerate=self.sample_rate, channels=channel_count) as m:
                    while self._recording:
                        data = m.record(numframes=chunk)
                        self._frames.append(data.copy())
                return
            except Exception as exc:
                last_exc = exc
                if self._frames:
                    return

        if last_exc is not None:
            raise RuntimeError(f"System audio capture failed: {last_exc}") from last_exc


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
            if sys.platform == "darwin" or sys.platform.startswith("linux"):
                for m in sc.all_microphones(include_loopback=False):
                    if _score_system_device_name(m.name) > 0:
                        devices.append({"name": m.name, "source": "system"})
        except Exception:
            pass
    return devices
