"""
Gemini Live API client for real-time voice conversations.
Supports ephemeral tokens, session resumption, context window compression,
automatic reconnection, and idle timeout.
"""

import asyncio
import base64
import json
import logging
import queue
import threading
import time
from typing import Optional, Callable

import numpy as np
import pyaudio
import sounddevice as sd
import websockets

logger = logging.getLogger("GeminiLive")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

GEMINI_LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
)

SAMPLE_RATE_INPUT = 16000
SAMPLE_RATE_OUTPUT = 24000
CHANNELS = 1
BLOCK_SIZE = 480
PLAYBACK_FRAMES = 1024
IDLE_CHECK_INTERVAL = 5.0
MAX_RECONNECT_RETRIES = 5
RECONNECT_BASE_DELAY = 1.0


class GeminiLiveClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        ephemeral_token: Optional[str] = None,
        model: str = "gemini-3.1-flash-live-preview",
        voice_name: str = "Puck",
        media_resolution: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_user_transcript: Optional[Callable[[str], None]] = None,
        tools: Optional[list[dict]] = None,
        on_tool_call: Optional[Callable[[str, dict], dict]] = None,
        system_instruction: Optional[str] = None,
        session_resumption: bool = False,
        context_compression: bool = False,
        idle_timeout: int = 300,
        thinking_level: Optional[str] = None,
        thinking_budget: Optional[int] = None,
    ):
        if not ephemeral_token and not api_key:
            raise ValueError("Either ephemeral_token or api_key must be provided")

        self.ephemeral_token = ephemeral_token
        self.api_key = api_key
        self.model = model
        self.voice_name = voice_name
        self.media_resolution = media_resolution
        self.on_status = on_status
        self.on_error = on_error
        self.on_transcript = on_transcript
        self.on_user_transcript = on_user_transcript
        self.tools = tools
        self.on_tool_call = on_tool_call
        self.system_instruction = system_instruction
        self.session_resumption = session_resumption
        self.context_compression = context_compression
        self.idle_timeout = idle_timeout
        self.thinking_level = thinking_level
        self.thinking_budget = thinking_budget

        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_thread: Optional[threading.Thread] = None

        self._input_queue: queue.Queue = queue.Queue()
        self._output_queue: queue.Queue = queue.Queue()
        self._interrupted = threading.Event()
        self._ai_speaking = threading.Event()

        self._playback_stream: Optional[pyaudio.Stream] = None
        self._py_audio: Optional[pyaudio.PyAudio] = None

        self._session_handle: Optional[str] = None
        self._message_index: int = 0
        self._last_activity_time: float = time.time()
        self._retry_count: int = 0
        self._reconnect_pending = False
        self._goaway_received = False

        self._gating_audio = threading.Event()
        self._tool_call_in_progress = threading.Event()

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._interrupted.clear()
        self._ai_speaking.clear()
        self._reconnect_pending = False
        self._goaway_received = False
        self._retry_count = 0
        self._message_index = 0
        logger.info(f"Starting Gemini Live Chat (Model: {self.model}, Voice: {self.voice_name})")
        self._main_thread = threading.Thread(target=self._run_async, daemon=True)
        self._main_thread.start()

    def stop(self) -> None:
        logger.info("Stopping Gemini Live Chat session...")
        self._running = False
        self._reconnect_pending = False
        if self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)
            except RuntimeError:
                pass

    def send_video_frame(self, image_bytes: bytes) -> None:
        if not self._running or not self._ws:
            return
        self._reset_idle_timer()
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        if "gemini-3.1" in self.model:
            msg = {
                "realtime_input": {
                    "video": {
                        "data": encoded,
                        "mime_type": "image/jpeg",
                    }
                }
            }
        else:
            msg = {
                "realtime_input": {
                    "media_chunks": [{
                        "data": encoded,
                        "mime_type": "image/jpeg",
                    }]
                }
            }
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(msg)), self._loop
            )

    def send_text(self, text: str) -> None:
        if not self._running or not self._ws:
            return
        self._reset_idle_timer()
        msg = {
            "client_content": {
                "turns": [{"role": "user", "parts": [{"text": text}]}],
                "turn_complete": True,
            }
        }
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(msg)), self._loop
            )

    def reset_idle_timer(self) -> None:
        self._reset_idle_timer()

    # ── Internal: async WebSocket lifecycle ──────────────────────────────────

    async def _close_ws(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
                logger.info("WebSocket connection closed gracefully.")
            except Exception as exc:
                logger.error(f"Error during WebSocket closure: {exc}")
            self._ws = None

    def _run_async(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        logger.info("Gemini Live async loop started.")
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as exc:
            logger.error(f"Gemini Live loop crash: {exc}", exc_info=True)
            self._safe_error(f"Gemini Live error: {exc}")
        finally:
            logger.info("Gemini Live async loop terminated.")
            self._loop.close()

    async def _async_main(self) -> None:
        while self._running:
            try:
                await self._connect_and_run()
                break
            except (websockets.WebSocketException, asyncio.TimeoutError, ConnectionError, OSError) as exc:
                if not self._running:
                    break
                logger.error(f"Connection dropped: {exc}")
                self._retry_count += 1
                if self._retry_count > MAX_RECONNECT_RETRIES:
                    logger.error(f"Max reconnection retries ({MAX_RECONNECT_RETRIES}) exceeded.")
                    self._safe_error("Connection lost after maximum retries.")
                    break
                wait = RECONNECT_BASE_DELAY * (2 ** (self._retry_count - 1))
                logger.info(f"Reconnecting in {wait:.1f}s (attempt {self._retry_count}/{MAX_RECONNECT_RETRIES})...")
                self._safe_status(f"Reconnecting ({self._retry_count}/{MAX_RECONNECT_RETRIES})...")
                await asyncio.sleep(wait)
            except Exception as exc:
                logger.error(f"Fatal connection error: {exc}")
                self._safe_error(f"Fatal: {exc}")
                break

    def _build_ws_url(self) -> str:
        if self.ephemeral_token:
            return f"{GEMINI_LIVE_WS_URL}?generativeai-token={self.ephemeral_token}"
        return f"{GEMINI_LIVE_WS_URL}?key={self.api_key}"

    def _build_setup_message(self) -> dict:
        setup_payload: dict = {
            "model": f"models/{self.model}",
            "generation_config": {
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {
                            "voice_name": self.voice_name,
                        }
                    },
                    "language_code": "en-US",
                },
            },
        }

        if self.session_resumption:
            setup_payload["session_resumption"] = {
                "transparent": True,
            }

        if self.context_compression:
            setup_payload["context_window_compression"] = {
                "trigger_tokens": 64000,
                "sliding_window": {
                    "target_tokens": 32000,
                },
            }

        thinking_cfg = {}
        if "3.1" in self.model and self.thinking_level:
            thinking_cfg["thinking_level"] = self.thinking_level
        elif self.thinking_budget is not None:
            thinking_cfg["thinking_budget"] = self.thinking_budget
        if thinking_cfg:
            setup_payload["generation_config"]["thinking_config"] = thinking_cfg

        if self._session_handle:
            setup_payload["session_id"] = self._session_handle

        if self.tools:
            setup_payload["tools"] = self.tools

        if self.system_instruction:
            setup_payload["system_instruction"] = {
                "parts": [{"text": self.system_instruction}]
            }

        return {"setup": setup_payload}

    async def _connect_and_run(self) -> None:
        url = self._build_ws_url()
        logger.info(f"Connecting to Gemini Live WebSocket...")

        async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
            self._ws = ws
            self._goaway_received = False
            logger.info("WebSocket connected. Sending setup message.")

            setup_msg = self._build_setup_message()
            await ws.send(json.dumps(setup_msg))

            raw = await ws.recv()
            reply = json.loads(raw)
            if "error" in reply:
                err_msg = reply["error"].get("message", "Setup failed")
                logger.error(f"Gemini setup rejected: {err_msg} | full reply: {reply}")
                raise ConnectionError(f"Gemini setup error: {err_msg}")

            logger.info("Gemini setup acknowledged. Session ready.")
            self._safe_status("Connected")
            self._reset_idle_timer()

            mic_thread = threading.Thread(target=self._run_mic, daemon=True)
            mic_thread.start()
            playback_thread = threading.Thread(target=self._run_playback, daemon=True)
            playback_thread.start()

            idle_task = asyncio.create_task(self._idle_monitor())

            send_task = asyncio.create_task(self._send_audio(ws))
            receive_task = asyncio.create_task(self._receive_audio(ws))

            done, pending = await asyncio.wait(
                [send_task, receive_task, idle_task],
                return_when=asyncio.FIRST_EXCEPTION,
            )

            for task in pending:
                task.cancel()

            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    raise exc

            if self._reconnect_pending:
                raise websockets.WebSocketException(1000, "Reconnect requested")

    # ── Idle monitor ─────────────────────────────────────────────────────────

    def _reset_idle_timer(self) -> None:
        self._last_activity_time = time.time()

    async def _idle_monitor(self) -> None:
        if self.idle_timeout <= 0:
            return
        while self._running:
            await asyncio.sleep(IDLE_CHECK_INTERVAL)
            elapsed = time.time() - self._last_activity_time
            if elapsed >= self.idle_timeout:
                logger.info(f"Idle timeout reached ({self.idle_timeout}s). Disconnecting.")
                self._safe_status("Idle timeout")
                self._running = False
                break

    # ── Mic capture ─────────────────────────────────────────────────────────

    def _mic_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning(f"Mic status: {status}")
        if self._running and not self._ai_speaking.is_set():
            self._input_queue.put(indata.copy())

    def _run_mic(self) -> None:
        logger.info("Starting mic capture thread.")
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_INPUT,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCK_SIZE,
                callback=self._mic_callback,
            ):
                while self._running:
                    sd.sleep(100)
        except Exception as exc:
            logger.error(f"Mic thread error: {exc}")
            self._safe_error(f"Mic error: {exc}")
        logger.info("Mic capture thread terminated.")

    # ── Audio playback ──────────────────────────────────────────────────────

    def _run_playback(self) -> None:
        logger.info("Starting playback thread.")
        self._py_audio = pyaudio.PyAudio()
        try:
            self._playback_stream = self._py_audio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE_OUTPUT,
                output=True,
                frames_per_buffer=PLAYBACK_FRAMES,
            )
            while self._running:
                try:
                    if self._gating_audio.is_set():
                        self._clear_queue(self._output_queue)
                        self._ai_speaking.clear()
                        sd.sleep(50)
                        continue

                    data = self._output_queue.get(timeout=0.2)
                    self._ai_speaking.set()
                    self._reset_idle_timer()

                    if self._interrupted.is_set():
                        logger.info("Interruption detected. Clearing playback.")
                        self._interrupted.clear()
                        self._clear_queue(self._output_queue)
                        self._ai_speaking.clear()
                        continue

                    self._playback_stream.write(
                        data.tobytes() if isinstance(data, np.ndarray) else data
                    )

                    if self._output_queue.empty():
                        self._ai_speaking.clear()

                except queue.Empty:
                    self._ai_speaking.clear()
                    continue

            self._playback_stream.stop_stream()
            self._playback_stream.close()
        except Exception as exc:
            if self._running:
                logger.error(f"Playback thread error: {exc}")
                self._safe_error(f"Playback error: {exc}")
        finally:
            self._ai_speaking.clear()
            if self._py_audio:
                self._py_audio.terminate()
        logger.info("Playback thread terminated.")

    @staticmethod
    def _clear_queue(q: queue.Queue) -> None:
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break

    # ── WebSocket send / receive ────────────────────────────────────────────

    async def _send_audio(self, ws: websockets.WebSocketClientProtocol) -> None:
        loop = asyncio.get_running_loop()
        logger.info("Starting audio send loop.")

        while self._running:
            try:
                data = await loop.run_in_executor(
                    None, lambda: self._input_queue.get(timeout=0.2)
                )
            except:
                continue

            self._reset_idle_timer()

            audio_bytes = (
                data.astype(np.int16).tobytes()
                if isinstance(data, np.ndarray)
                else data
            )
            encoded = base64.b64encode(audio_bytes).decode("utf-8")

            if self._message_index == 0:
                self._message_index = 1

            try:
                if "gemini-3.1" in self.model:
                    await ws.send(json.dumps({
                        "realtime_input": {
                            "audio": {
                                "data": encoded,
                                "mime_type": "audio/pcm;rate=16000",
                            }
                        }
                    }))
                else:
                    await ws.send(json.dumps({
                        "realtime_input": {
                            "media_chunks": [{
                                "data": encoded,
                                "mime_type": "audio/pcm;rate=16000",
                            }]
                        }
                    }))
                self._message_index += 1
            except Exception as exc:
                logger.error(f"Send audio error: {exc}")
                self._safe_error(f"Send error: {exc}")
                break
        logger.info("Audio send loop terminated.")

    async def _receive_audio(self, ws: websockets.WebSocketClientProtocol) -> None:
        logger.info("Starting receive audio loop.")
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except websockets.WebSocketException:
                raise
            except Exception as exc:
                logger.error(f"Receive recv error: {exc}")
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if "error" in msg:
                err = msg["error"].get("message", "API error")
                logger.error(f"Gemini API error: {err}")
                self._safe_error(err)
                break

            # goAway — server is about to disconnect (60s warning)
            if "goAway" in msg:
                reason = msg["goAway"].get("reason", "unknown")
                logger.info(f"Received goAway from server (reason: {reason}). Scheduling reconnection.")
                self._goaway_received = True
                self._reconnect_pending = True
                self._safe_status("Session ending, reconnecting...")
                break

            # Session resumption update — store the handle
            if "session_resumption_update" in msg:
                sru = msg["session_resumption_update"]
                if sru.get("resumable") and sru.get("new_handle"):
                    self._session_handle = sru["new_handle"]
                    logger.info(f"Session resumption handle updated.")

            # Interrupted
            if "interrupted" in msg:
                logger.info("Gemini reported user interruption.")
                self._interrupted.set()
                self._reset_idle_timer()
                self._safe_status("Interrupted")
                continue

            # Tool Call
            if "toolCall" in msg:
                logger.info("Received tool call from Gemini.")
                await self._handle_tool_call(ws, msg["toolCall"])
                continue

            # Setup Complete
            if "setupComplete" in msg:
                logger.info("Gemini server confirmed setup complete.")
                self._safe_status("Listening")
                continue

            # Server Content
            if "serverContent" in msg:
                sc = msg["serverContent"]
                self._reset_idle_timer()

                if sc.get("turnComplete"):
                    logger.info("Gemini turn complete.")
                    self._safe_status("Listening")

                # Input transcription (what the user said)
                if "input_transcription" in sc:
                    input_text = sc["input_transcription"].get("text", "")
                    if input_text and self.on_user_transcript:
                        try:
                            self.on_user_transcript(input_text)
                        except Exception as exc:
                            logger.error(f"User transcript callback error: {exc}")

                # Output transcription (what Gemini said)
                if "output_transcription" in sc:
                    output_text = sc["output_transcription"].get("text", "")
                    if output_text and self.on_transcript:
                        try:
                            self.on_transcript(output_text)
                        except Exception as exc:
                            logger.error(f"Transcript callback error: {exc}")

                for part in sc.get("modelTurn", {}).get("parts", []):
                    if "inlineData" in part:
                        mime = part["inlineData"].get("mimeType", "")
                        if mime.startswith("audio/"):
                            if self._gating_audio.is_set():
                                logger.debug("Audio gating active — dropping audio chunk.")
                            else:
                                raw_audio = base64.b64decode(part["inlineData"]["data"])
                                arr = np.frombuffer(raw_audio, dtype=np.int16)
                                self._output_queue.put(arr)

                    if "text" in part:
                        text_part = part["text"]
                        logger.info(f"Gemini partial transcript: {text_part}")
                        if self.on_transcript:
                            try:
                                self.on_transcript(text_part)
                            except Exception as exc:
                                logger.error(f"Transcript callback error: {exc}")

        logger.info("Receive audio loop terminated.")

    async def _handle_tool_call(self, ws: websockets.WebSocketClientProtocol, tool_call: dict) -> None:
        function_calls = tool_call.get("functionCalls", [])
        self._tool_call_in_progress.set()

        function_responses = []

        for fc in function_calls:
            name = fc.get("name")
            args = fc.get("args", {})
            call_id = fc.get("id")

            logger.info(f"Gemini Tool Call: {name}({args})")

            result = {"success": False, "error": "No handler configured"}
            if self.on_tool_call:
                try:
                    result = self.on_tool_call(name, args)
                except Exception as exc:
                    logger.error(f"Error executing tool {name}: {exc}")
                    result = {"success": False, "error": str(exc)}

            function_responses.append({
                "id": call_id,
                "name": name,
                "response": {
                    "output": result
                },
                "scheduling": "SILENT"
            })

        # Send toolResponse back to the WebSocket!
        if function_responses:
            try:
                response_msg = {
                    "toolResponse": {
                        "functionResponses": function_responses
                    }
                }
                await ws.send(json.dumps(response_msg))
                logger.info(f"Sent tool responses to Gemini: {function_responses}")
            except Exception as exc:
                logger.error(f"Error sending tool responses: {exc}")

        self._tool_call_in_progress.clear()

        self._gating_audio.set()
        self._clear_queue(self._output_queue)

        def _release_gate():
            time.sleep(0.6)
            self._gating_audio.clear()
            logger.debug("Audio gating released.")

        threading.Thread(target=_release_gate, daemon=True).start()

    # ── Safe callbacks ──────────────────────────────────────────────────────

    def _safe_status(self, status: str) -> None:
        if self.on_status:
            try:
                self.on_status(status)
            except Exception as exc:
                logger.error(f"Status callback error: {exc}")

    def _safe_error(self, error: str) -> None:
        logger.error(f"Error callback: {error}")
        if self.on_error:
            try:
                self.on_error(error)
            except Exception as exc:
                logger.error(f"Error callback error: {exc}")
