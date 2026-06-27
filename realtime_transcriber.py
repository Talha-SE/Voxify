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
import logging
import threading
from typing import Callable, Optional

import recorder as rec_module

logger = logging.getLogger("realtime_transcriber")

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
        mic_device: Optional[int | str] = None,
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
        self.mic_device       = mic_device  # sounddevice device ID (int) or name pattern (str)
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
            logger.warning("start() called but already running")
            return
        self._stop_flag.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("RealtimeTranscriber started in background thread")

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
            logger.error(f"Unhandled exception in _run_loop: {exc}", exc_info=True)
            if self.on_error:
                self.on_error(str(exc))
        finally:
            self._running = False
            try:
                # Cancel all pending tasks
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                
                if pending:
                    # Give tasks a chance to finish cancellation
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                
                # Shutdown async generators and the loop
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            except Exception:
                pass
            finally:
                loop.close()

    async def _async_main(self) -> None:
        # ── Import realtime types (requires the realtime dependency) ───
        try:
            from mistralai import Mistral
            from mistralai.client.models import (
                AudioFormat,
                RealtimeTranscriptionSessionCreated,
                RealtimeTranscriptionSessionUpdated,
                TranscriptionStreamTextDelta,
                TranscriptionStreamDone,
                RealtimeTranscriptionError,
            )
            from mistralai.extra.realtime import UnknownRealtimeEvent
        except ImportError as exc:
            if self.on_error:
                self.on_error(
                    f"Realtime package missing.\n"
                    f"Install the realtime dependency.\n{exc}"
                )
            return

        # ── Verify audio dependencies ───────────────────────────────────────
        if self.source == "mic":
            try:
                import sounddevice as _sd
            except ImportError:
                if self.on_error:
                    self.on_error(
                        "sounddevice is not installed.\nRun:  pip install sounddevice"
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

        logger.info("Creating Mistral client and starting transcribe_stream")
        client      = Mistral(api_key=self.api_key)
        audio_fmt   = AudioFormat(
            encoding="pcm_s16le", sample_rate=self.sample_rate
        )
        audio_stream = self._iter_microphone()
        logger.info("Audio stream created, entering transcribe_stream loop")

        try:
            async for event in client.audio.realtime.transcribe_stream(
                audio_stream=audio_stream,
                model=self.model,
                audio_format=audio_fmt,
            ):
                if self._stop_flag.is_set():
                    logger.info("Stop flag set, breaking out of stream loop")
                    break

                if isinstance(event, RealtimeTranscriptionSessionCreated):
                    logger.info("Session created event received")
                    if self.on_status:
                        self.on_status("Live — speak now")

                elif isinstance(event, RealtimeTranscriptionSessionUpdated):
                    logger.info("Session updated event received")
                    # Session parameters confirmed — no action needed

                elif isinstance(event, TranscriptionStreamTextDelta):
                    logger.info(f"Text delta received: '{event.text}' (len={len(event.text)})")
                    if self.on_delta and event.text:
                        self.on_delta(event.text)

                elif isinstance(event, TranscriptionStreamDone):
                    logger.info("Stream done event received")
                    break

                elif isinstance(event, RealtimeTranscriptionError):
                    logger.error(f"Realtime error event: {event.error}")
                    if self.on_error:
                        self.on_error(str(event.error))
                    break

                elif isinstance(event, UnknownRealtimeEvent):
                    logger.warning(f"Unknown event type received, ignoring")
                    continue  # ignore unknown events

                else:
                    logger.warning(f"Unexpected event type: {type(event).__name__}")

        except Exception as exc:
            logger.error(f"Exception in transcribe_stream: {exc}", exc_info=True)
            if not self._stop_flag.is_set() and self.on_error:
                self.on_error(str(exc))
        finally:
            logger.info("transcribe_stream ended, calling on_done")
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
        """Capture from microphone via sounddevice with gain normalization."""
        import sounddevice as sd
        import numpy as np
        chunk_samples = int(self.sample_rate * self.chunk_duration_ms / 1000)
        loop = asyncio.get_running_loop()

        # Target RMS level: -16 dBFS (good conversational level)
        # 16-bit peak = 32768;  -16 dB = 32768 * 10^(-16/20) ≈ 5189
        TARGET_RMS = 5189
        MAX_GAIN   = 4.0   # 12 dB cap to avoid amplifying noise too much

        # Use a queue to bridge the blocking callback with the async generator
        import queue as _queue
        audio_queue: _queue.Queue = _queue.Queue()
        self._chunks_captured = 0

        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"sounddevice status: {status}")
            # indata is (frames, channels) int16 numpy array — may be read-only
            samples = np.frombuffer(indata, dtype=np.int16).copy()
            # ── Measure RMS level (every chunk for first 5, then every 50th) ──
            rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
            if self._chunks_captured <= 5 or self._chunks_captured % 50 == 0:
                dbfs = 20 * np.log10(rms / 32768.0) if rms > 0 else -100
                logger.info(
                    f"Audio level: {dbfs:.1f} dBFS  (chunk #{self._chunks_captured})"
                )
            # ── Apply gain if quiet (normalise toward -16 dBFS) ──────────────
            if 0 < rms < TARGET_RMS:
                gain = min(TARGET_RMS / rms, MAX_GAIN)
                samples = np.clip(
                    samples.astype(np.float64) * gain, -32768, 32767
                ).astype(np.int16)
            audio_queue.put(samples.tobytes())

        # ── Resolve microphone device ────────────────────────────────────────
        device_kw = {}
        if self.mic_device is not None:
            device_kw["device"] = self.mic_device
            logger.info(f"Using explicit mic device: {self.mic_device}")
        else:
            logger.info(f"Using default mic device (index {sd.default.device[0]})")

        logger.info(f"Starting RawInputStream: sr={self.sample_rate}, channels=1, blocksize={chunk_samples}")
        stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=chunk_samples,
            **device_kw,
            callback=callback,
        )
        try:
            stream.start()
            logger.info("RawInputStream started successfully")
            while not self._stop_flag.is_set():
                try:
                    data = await loop.run_in_executor(
                        None, audio_queue.get, True, 0.1
                    )
                    self._chunks_captured += 1
                    if self._chunks_captured <= 3 or self._chunks_captured % 50 == 0:
                        logger.info(f"Audio chunk #{self._chunks_captured}: {len(data)} bytes")
                    yield data
                except _queue.Empty:
                    if self._chunks_captured == 0:
                        logger.warning("No audio data captured yet (queue empty)")
                    continue
        finally:
            logger.info(f"Stopping RawInputStream after {self._chunks_captured} chunks")
            stream.stop()
            stream.close()

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
