"""Server-side API configuration helpers.

This module loads sensitive API settings from environment variables.
Do not import this file in frontend code.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def get_mistral_api_key() -> str:
    """Return the private Mistral API key from environment variables."""
    api_key = (os.getenv("MISTRAL_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY is missing in website/.env")
    return api_key


def get_mistral_model() -> str:
    """Return the default model name for server-side API calls."""
    return (os.getenv("MISTRAL_MODEL") or "voxtral-mini-2602").strip()


def get_masked_api_key() -> str:
    """Return a masked API key for admin diagnostics only."""
    key = (os.getenv("MISTRAL_API_KEY") or "").strip()
    if len(key) < 8:
        return "not-configured"
    return f"{key[:4]}...{key[-4:]}"


def get_gemini_api_key() -> str:
    """Return the private Gemini API key from environment variables."""
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing in website/.env")
    return api_key


def get_gemini_model() -> str:
    """Return the default Gemini model for the Live API."""
    return (os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-live-preview").strip()
