"""
Gemini Live API client for real-time voice conversations.
Streams mic audio to Gemini Live API over WebSocket and plays back
audio responses directly — no separate STT/TTS pipeline needed.
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

# Professional Logging Setup
logger = logging.getLogger("GeminiLive")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

GEMINI_LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
)

SAMPLE_RATE_INPUT = 16000
SAMPLE_RATE_OUTPUT = 24000
CHANNELS = 1
BLOCK_SIZE = 480  # 30ms at 16kHz
PLAYBACK_FRAMES = 1024


class GeminiLiveClient:
    """Manages a WebSocket connection to the Gemini Live API for real-time
    audio-in/audio-out conversation."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.1-flash-live-preview",
        voice_name: str = "Puck",
        on_status: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        tools: Optional[list[dict]] = None,
        on_tool_call: Optional[Callable[[str, dict], dict]] = None,
        system_instruction: Optional[str] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.voice_name = voice_name
        self.on_status = on_status
        self.on_error = on_error
        self.on_transcript = on_transcript
        self.tools = tools
        self.on_tool_call = on_tool_call
        self.system_instruction = system_instruction

        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_thread: Optional[threading.Thread] = None

        self._input_queue: queue.Queue = queue.Queue()
        self._video_queue: queue.Queue = queue.Queue()
        self._output_queue: queue.Queue = queue.Queue()
        self._interrupted = threading.Event()
        self._ai_speaking = threading.Event()

        self._playback_stream: Optional[pyaudio.Stream] = None
        self._py_audio: Optional[pyaudio.PyAudio] = None

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the Live API session in a background thread."""
        if self._running:
            return
        self._running = True
        self._interrupted.clear()
        self._ai_speaking.clear()
        logger.info(f"Starting Gemini Live Chat (Model: {self.model}, Voice: {self.voice_name})")
        self._main_thread = threading.Thread(target=self._run_async, daemon=True)
        self._main_thread.start()

    def stop(self) -> None:
        """Stop the Live API session."""
        logger.info("Stopping Gemini Live Chat session...")
        self._running = False
        if self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)
            except RuntimeError:
                pass

    def send_video_frame(self, image_bytes: bytes) -> None:
        """Send a JPEG video frame to the Live API."""
        if not self._running or not self._ws:
            return
        
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
            asyncio.run_coroutine_threadsafe(self._ws.send(json.dumps(msg)), self._loop)

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
        url = f"{GEMINI_LIVE_WS_URL}?key={self.api_key}"
        logger.info(f"Connecting to Gemini Live WebSocket: {GEMINI_LIVE_WS_URL}")

        try:
            async with websockets.connect(url) as ws:
                self._ws = ws
                logger.info("WebSocket connected. Sending setup message.")

                # Send setup
                setup_msg = {
                    "setup": {
                        "model": f"models/{self.model}",
                        "generation_config": {
                            "response_modalities": ["AUDIO"],
                            "speech_config": {
                                "voice_config": {
                                    "prebuilt_voice_config": {
                                        "voice_name": self.voice_name,
                                    }
                                }
                            },
                        },
                    }
                }
                if self.tools:
                    setup_msg["setup"]["tools"] = self.tools
                
                if self.system_instruction:
                    setup_msg["setup"]["system_instruction"] = {
                        "parts": [{"text": self.system_instruction}]
                    }
                
                await ws.send(json.dumps(setup_msg))

                # Wait for setup acknowledgement
                raw = await ws.recv()
                reply = json.loads(raw)
                if "error" in reply:
                    err_msg = reply["error"].get("message", "Setup failed")
                    logger.error(f"Gemini setup error: {err_msg}")
                    self._safe_error(f"Gemini Live setup error: {err_msg}")
                    return

                logger.info("Gemini setup acknowledged. Session ready.")
                self._safe_status("Connected")

                # Start mic capture and playback
                mic_thread = threading.Thread(target=self._run_mic, daemon=True)
                mic_thread.start()
                playback_thread = threading.Thread(target=self._run_playback, daemon=True)
                playback_thread.start()

                # Run send / receive concurrently
                send_task = asyncio.create_task(self._send_audio(ws))
                receive_task = asyncio.create_task(self._receive_audio(ws))
                done, pending = await asyncio.wait(
                    [send_task, receive_task],
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                
                # Cancel pending tasks to ensure clean shutdown
                for task in pending:
                    task.cancel()
                
                for task in done:
                    exc = task.exception()
                    if exc:
                        logger.error(f"Task error causing disconnect: {exc}")
                        self._safe_error(f"Live connection lost: {exc}")
        except Exception as exc:
            logger.error(f"WebSocket connection error: {exc}")
            self._safe_error(f"WebSocket error: {exc}")

    # ── Mic capture ─────────────────────────────────────────────────────────

    def _mic_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning(f"Mic status: {status}")
        
        # ECHO SUPPRESSION: Only queue mic audio if AI isn't speaking
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
                    data = self._output_queue.get(timeout=0.2)
                    
                    # Signal AI is speaking for echo suppression
                    self._ai_speaking.set()
                    
                    if self._interrupted.is_set():
                        logger.info("Interruption event detected. Clearing playback buffer.")
                        self._interrupted.clear()
                        self._clear_queue(self._output_queue)
                        self._ai_speaking.clear()
                        continue
                    
                    self._playback_stream.write(
                        data.tobytes() if isinstance(data, np.ndarray) else data
                    )
                    
                    # If queue is empty, AI is done speaking for now
                    if self._output_queue.empty():
                        self._ai_speaking.clear()
                        
                except queue.Empty:
                    self._ai_speaking.clear()
                    continue

            self._playback_stream.stop_stream()
            self._playback_stream.close()
        except Exception as exc:
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

            audio_bytes = (
                data.astype(np.int16).tobytes()
                if isinstance(data, np.ndarray)
                else data
            )
            encoded = base64.b64encode(audio_bytes).decode("utf-8")

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
            except Exception as exc:
                logger.error(f"Receive loop recv error: {exc}")
                self._safe_error(f"Receive error: {exc}")
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.error("JSON decode error from Gemini.")
                continue

            if "error" in msg:
                err = msg["error"].get("message", "API error")
                logger.error(f"Gemini API error: {err}")
                self._safe_error(err)
                break

            # Handle Interrupted
            if "interrupted" in msg:
                logger.info("Gemini reported user interruption.")
                self._interrupted.set()
                self._safe_status("Interrupted")
                continue

            # Handle Tool Call
            if "toolCall" in msg:
                logger.info("Received tool call from Gemini.")
                await self._handle_tool_call(ws, msg["toolCall"])
                continue

            # Handle Setup Complete
            if "setupComplete" in msg:
                logger.info("Gemini server confirmed setup complete.")
                self._safe_status("Listening")
                continue

            # Handle Server Content
            if "serverContent" in msg:
                sc = msg["serverContent"]

                if sc.get("turnComplete"):
                    logger.info("Gemini turn complete.")
                    self._safe_status("Listening")

                for part in sc.get("modelTurn", {}).get("parts", []):
                    if "inlineData" in part:
                        mime = part["inlineData"].get("mimeType", "")
                        if mime.startswith("audio/"):
                            logger.debug("Received audio content from Gemini.")
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
        """Process function calls from Gemini and send responses back."""
        function_calls = tool_call.get("functionCalls", [])
        responses = []

        for fc in function_calls:
            name = fc.get("name")
            call_id = fc.get("id")
            args = fc.get("args", {})

            logger.info(f"Gemini Tool Call: {name}({args}) [ID: {call_id}]")
            
            result = {"success": False, "error": "No handler configured"}
            if self.on_tool_call:
                try:
                    # Execute locally (e.g., via output_handler)
                    result = self.on_tool_call(name, args)
                except Exception as exc:
                    logger.error(f"Error executing tool {name}: {exc}")
                    result = {"success": False, "error": str(exc)}
            
            responses.append({
                "name": name,
                "id": call_id,
                "response": result
            })

        if responses:
            resp_msg = {
                "toolResponse": {
                    "functionResponses": responses
                }
            }
            logger.info(f"Sending tool responses back to Gemini.")
            await ws.send(json.dumps(resp_msg))

    # ── Safe callbacks ──────────────────────────────────────────────────────

    def _safe_status(self, status: str) -> None:
        logger.info(f"Status callback: {status}")
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

    def _safe_transcript(self, transcript: str) -> None:
        if self.on_transcript:
            try:
                self.on_transcript(transcript)
            except Exception:
                pass
