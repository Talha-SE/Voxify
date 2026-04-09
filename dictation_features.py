"""Dictation post-processing utilities for profiles, replacements, and commands."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


VOICE_COMMAND_INSERTS = {
    "new line": "\n",
    "new paragraph": "\n\n",
    "insert bullet list": "\n- ",
}
VOICE_COMMAND_ACTIONS = {
    "undo last": "undo_last",
}
FILLER_WORDS = (
    "um",
    "uh",
    "you know",
    "like",
    "actually",
    "basically",
)


@dataclass(frozen=True)
class ProcessedTranscript:
    text: str
    actions: tuple[str, ...]


def _clean_spaces(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _remove_fillers(text: str, aggressive: bool) -> str:
    words = FILLER_WORDS if aggressive else FILLER_WORDS[:3]
    for filler in words:
        text = re.sub(rf"\b{re.escape(filler)}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _apply_profile(text: str, profile: str) -> str:
    profile = (profile or "notes").strip().lower()
    if profile == "chat":
        return _clean_spaces(text)
    if profile == "email":
        text = _remove_fillers(text, aggressive=False)
        text = _clean_spaces(text)
        if text and text[-1] not in ".!?":
            text = f"{text}."
        return text[:1].upper() + text[1:] if text else text
    if profile == "docs":
        text = _remove_fillers(text, aggressive=True)
        text = _clean_spaces(text)
        if text and text[-1] not in ".!?":
            text = f"{text}."
        return text[:1].upper() + text[1:] if text else text
    if profile == "code_notes":
        return text.strip()
    return _clean_spaces(text)


def _apply_replacements(text: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return text

    normalized_items = [
        ((key or "").strip(), (value or "").strip())
        for key, value in replacements.items()
        if (key or "").strip() and (value or "").strip()
    ]
    normalized_items.sort(key=lambda item: len(item[0]), reverse=True)

    for key, value in normalized_items:
        pattern = rf"\b{re.escape(key)}\b"
        text = re.sub(pattern, value, text, flags=re.IGNORECASE)
    return text


def _apply_personal_dictionary(text: str, dictionary_terms: Iterable[str]) -> str:
    canonical = {}
    for term in dictionary_terms:
        clean_term = (term or "").strip()
        if clean_term:
            canonical[clean_term.lower()] = clean_term
    if not canonical:
        return text

    pattern = r"\b(" + "|".join(re.escape(k) for k in sorted(canonical.keys(), key=len, reverse=True)) + r")\b"
    return re.sub(pattern, lambda m: canonical.get(m.group(1).lower(), m.group(0)), text, flags=re.IGNORECASE)


def _extract_prefixed_command(text: str, command_prefix: str) -> tuple[str, tuple[str, ...], bool]:
    clean_prefix = (command_prefix or "command").strip().lower()
    stripped = text.strip()
    lowered = stripped.lower()
    if not clean_prefix or not lowered.startswith(clean_prefix + " "):
        return text, tuple(), False

    cmd = lowered[len(clean_prefix):].strip()
    if cmd in VOICE_COMMAND_ACTIONS:
        return "", (VOICE_COMMAND_ACTIONS[cmd],), True
    if cmd in VOICE_COMMAND_INSERTS:
        return VOICE_COMMAND_INSERTS[cmd], tuple(), True
    return text, tuple(), False


def _inline_commands(text: str) -> str:
    for phrase, replacement in VOICE_COMMAND_INSERTS.items():
        text = re.sub(rf"\b{re.escape(phrase)}\b", replacement, text, flags=re.IGNORECASE)
    return text


def process_transcript(
    raw_text: str,
    profile: str = "notes",
    replacements: dict[str, str] | None = None,
    personal_dictionary: list[str] | None = None,
    voice_commands_enabled: bool = True,
    command_prefix: str = "command",
) -> ProcessedTranscript:
    text = raw_text or ""
    actions: tuple[str, ...] = tuple()

    if voice_commands_enabled:
        text, prefixed_actions, consumed = _extract_prefixed_command(text, command_prefix)
        actions = prefixed_actions
        if not consumed:
            text = _inline_commands(text)

    text = _apply_replacements(text, replacements or {})
    text = _apply_personal_dictionary(text, personal_dictionary or [])
    text = _apply_profile(text, profile=profile)
    return ProcessedTranscript(text=text, actions=actions)

