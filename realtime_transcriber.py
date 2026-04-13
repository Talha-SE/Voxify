"""
Real-time transcription engine.

Streams mic or system audio in real time and calls UI callbacks on every
text delta so the transcript updates live.

Usage
-----
    rt = RealtimeTranscriber(
        api_key="...",
        source="mic",  # or "system"
        on_delta=lambda t: print(t, end="", flush=True),
        on_status=lambda s: print(s),
        on_done=lambda: print("done"),
        on_error=lambda e: print("error:", e),
    )
    rt.start()
    # ... user speaks ...
    rt.stop()
"""

import asyncio
import threading
from typing import Callable, Optional

import recorder as rec_module

REALTIME_MODEL   = "voxtral-mini-transcribe-realtime-2602"
DEFAULT_SR       = 16_000
DEFAULT_CHUNK_MS = 120   # ms per mic chunk


class RealtimeTranscriber:
    """
    Thread-safe wrapper around `client.audio.realtime.transcribe_stream`.

    A dedicated asyncio event loop runs in a daemon thread.
    All callbacks fire from that thread; callers should marshal to the
    main thread (e.g. via `root.after(0, cb)` in tkinter).
    """

    def __init__(
        self,
        api_key: str,
        model: str = REALTIME_MODEL,
        sample_rate: int = DEFAULT_SR,
        chunk_duration_ms: int = DEFAULT_CHUNK_MS,
        source: str = "mic",  # "mic" or "system"
        on_delta:  Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_done:   Optional[Callable[[], None]]    = None,
        on_error:  Optional[Callable[[str], None]] = None,
    ) -> None:
        self.api_key          = api_key
        self.model            = model
        self.sample_rate      = sample_rate
        self.chunk_duration_ms = chunk_duration_ms
        self.source           = source  # "mic" or "system"
        self.on_delta  = on_delta
        self.on_status = on_status
        self.on_done   = on_done
        self.on_error  = on_error

        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running  = False

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin streaming mic audio and transcribing in a background thread."""
        if self._running:
            return
        self._stop_flag.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the mic generator to stop; transcription ends gracefully."""
        self._stop_flag.set()
        self._running = False
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Internal ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_main())
        except Exception as exc:
            if self.on_error:
                self.on_error(str(exc))
        finally:
            self._running = False
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _async_main(self) -> None:
        # ── Import realtime types (requires the realtime dependency) ───
        try:
            from mistralai import Mistral
            from mistralai.models import (
                AudioFormat,
                RealtimeTranscriptionSessionCreated,
                TranscriptionStreamTextDelta,
                TranscriptionStreamDone,
                RealtimeTranscriptionError,
            )
            from mistralai.extra.realtime import UnknownRealtimeEvent
        except ImportError as exc:
            if self.on_error:
                self.on_error(
                    f"Realtime package missing.\n"
                    f"Install the realtime dependency and pyaudio.\n{exc}"
                )
            return

        # ── Verify audio dependencies ───────────────────────────────────────
        if self.source == "mic":
            try:
                import pyaudio as _pa
            except ImportError:
                if self.on_error:
                    self.on_error(
                        "pyaudio is not installed.\nRun:  pip install pyaudio"
                    )
                return
        else:
            try:
                import soundcard as _sc
            except ImportError:
                if self.on_error:
                    self.on_error(
                        "soundcard is not installed.\nRun:  pip install soundcard"
                    )
                return

        if self.on_status:
            self.on_status("🔌 Connecting…")

        client      = Mistral(api_key=self.api_key)
        audio_fmt   = AudioFormat(
            encoding="pcm_s16le", sample_rate=self.sample_rate
        )
        audio_stream = self._iter_microphone()

        try:
            async for event in client.audio.realtime.transcribe_stream(
                audio_stream=audio_stream,
                model=self.model,
                audio_format=audio_fmt,
            ):
                if self._stop_flag.is_set():
                    break

                if isinstance(event, RealtimeTranscriptionSessionCreated):
                    if self.on_status:
                        self.on_status("🔴 Live — speak now…")

                elif isinstance(event, TranscriptionStreamTextDelta):
                    if self.on_delta and event.text:
                        self.on_delta(event.text)

                elif isinstance(event, TranscriptionStreamDone):
                    break

                elif isinstance(event, RealtimeTranscriptionError):
                    if self.on_error:
                        self.on_error(str(event.error))
                    break

                elif isinstance(event, UnknownRealtimeEvent):
                    continue  # ignore unknown events

        except Exception as exc:
            if not self._stop_flag.is_set() and self.on_error:
                self.on_error(str(exc))
        finally:
            if self.on_done:
                self.on_done()

    async def _iter_microphone(self):
        """
        Async generator: yields raw PCM chunks from the audio source.
        Supports both mic (pyaudio) and system (soundcard loopback).
        Stops when `_stop_flag` is set.
        """
        if self.source == "mic":
            async for chunk in self._iter_mic_audio():
                yield chunk
        else:
            async for chunk in self._iter_system_audio():
                yield chunk

    async def _iter_mic_audio(self):
        """Capture from microphone via pyaudio."""
        import pyaudio
        p              = pyaudio.PyAudio()
        chunk_samples  = int(self.sample_rate * self.chunk_duration_ms / 1000)
        stream         = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=chunk_samples,
        )
        loop = asyncio.get_running_loop()
        try:
            while not self._stop_flag.is_set():
                # Run blocking read off the event-loop thread
                data = await loop.run_in_executor(
                    None, stream.read, chunk_samples, False
                )
                yield data
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

    async def _iter_system_audio(self):
        """Capture system/loopback audio via soundcard."""
        try:
            import numpy as np
        except ImportError:
            if self.on_error:
                self.on_error("soundcard not installed. Run: pip install soundcard")
            return

        try:
            mic = rec_module._resolve_system_microphone()

            chunk_samples = int(self.sample_rate * self.chunk_duration_ms / 1000)
            loop = asyncio.get_running_loop()

            last_exc: Exception | None = None
            for channel_count in rec_module._system_channel_candidates(1):
                try:
                    with mic.recorder(samplerate=self.sample_rate, channels=channel_count) as recorder:
                        while not self._stop_flag.is_set():
                            # Run blocking read off-thread
                            data = await loop.run_in_executor(
                                None, recorder.record, chunk_samples
                            )
                            if getattr(data, "ndim", 1) > 1:
                                data = data.mean(axis=1)
                            # Convert float32 to int16 PCM
                            audio_int16 = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
                            yield audio_int16.tobytes()
                    return
                except Exception as exc:
                    last_exc = exc

            if last_exc is not None and self.on_error and not self._stop_flag.is_set():
                self.on_error(f"System audio error: {last_exc}")
        except Exception as exc:
            if self.on_error and not self._stop_flag.is_set():
                self.on_error(f"System audio error: {exc}")
