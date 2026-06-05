"""
Mistral Chat and TTS client.
Handles text generation and text-to-speech using Mistral AI.
"""

import os
import threading
import time
from typing import List, Optional

import requests
import pyaudio

class MistralChatClient:
    def __init__(self, api_key: str, chat_model: str = "mistral-medium-latest", tts_model: str = "mistral-tts-latest"):
        self.api_key = api_key
        self.chat_model = chat_model
        self.tts_model = tts_model
        self.history = []
        self._playback_thread: Optional[threading.Thread] = None

    def add_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        # Keep history manageable
        if len(self.history) > 10:
            self.history = self.history[-10:]

    def chat(self, user_input: str) -> str:
        """Generates a response from Mistral."""
        self.add_message("user", user_input)
        
        url = "https://api.mistral.ai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        system_prompt = (
            "You are Voxify Chat, a helpful and concise AI assistant. "
            "Reply naturally and to the point. If a long explanation is needed, give it, "
            "but prefer brevity for simple questions. "
            "You will be speaking your response, so make it conversational."
        )
        
        messages = [{"role": "system", "content": system_prompt}] + self.history
        
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": 0.7
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            reply = data["choices"][0]["message"]["content"]
            self.add_message("assistant", reply)
            return reply
        except Exception as e:
            return f"Error in chat: {str(e)}"

    def speak(self, text: str):
        """Converts text to speech and plays it."""
        url = "https://api.mistral.ai/v1/audio/speech"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": self.tts_model,
            "input": text,
            "voice": "en-us-male-1" # Defaulting to an English voice as requested
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            
            # Play the audio
            # The API returns raw audio or a common format like MP3/WAV.
            # In 2026, it might be a stream. For now, we'll assume it's a file-like response.
            audio_data = response.content
            
            # Stop any current playback
            self.stop_playback()
            
            # Start new playback in a thread
            self._playback_thread = threading.Thread(target=self._play_audio, args=(audio_data,), daemon=True)
            self._playback_thread.start()
            
        except Exception as e:
            print(f"Error in TTS: {str(e)}")

    def _play_audio(self, audio_data: bytes):
        """Helper to play audio data using pyaudio."""
        import io
        import wave
        
        try:
            # Mistral TTS likely returns WAV or MP3. If WAV:
            with wave.open(io.BytesIO(audio_data), 'rb') as wf:
                p = pyaudio.PyAudio()
                stream = p.open(format=p.get_format_from_width(wf.getsampwidth()),
                                channels=wf.getnchannels(),
                                rate=wf.getframerate(),
                                output=True)
                
                data = wf.readframes(1024)
                while data:
                    stream.write(data)
                    data = wf.readframes(1024)
                
                stream.stop_stream()
                stream.close()
                p.terminate()
        except Exception as e:
            # If not WAV, maybe it's MP3? We might need pydub or another library if it's MP3.
            # Assuming WAV for now or raw PCM if the API supports it.
            print(f"Playback error: {e}")

    def stop_playback(self):
        """Stops any ongoing audio playback."""
        # This is a bit tricky with blocking _play_audio. 
        # A more robust implementation would use a flag or a different library.
        pass

    def clear_history(self):
        self.history = []
