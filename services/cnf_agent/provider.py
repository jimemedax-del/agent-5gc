"""Model port and deterministic replay adapter. No model SDK or network calls."""
from typing import Protocol

from pydantic import Field

from .schemas import Contract, Extraction, Message, ModelReply


class ModelProvider(Protocol):
    def extract(self, messages: tuple[Message, ...], max_tokens: int) -> ModelReply: ...


class ProviderCallError(RuntimeError):
    """Safe diagnostic plus billed usage, never an SDK exception body or key."""

    def __init__(self, code: str, tokens: int = 0, metadata: dict | None = None):
        super().__init__(code)
        self.tokens = tokens
        self.metadata = metadata or {}


class ReplayFrame(Contract):
    message: str
    extraction: Extraction
    tokens: int = Field(default=64, ge=0)


class ReplayProvider:
    """Return recorded extractions only for the exact scripted conversation."""

    def __init__(self, frames: tuple[ReplayFrame, ...]):
        self.frames = frames

    def extract(self, messages: tuple[Message, ...], max_tokens: int) -> ModelReply:
        index = len(messages) - 1
        if index < 0 or index >= len(self.frames):
            raise ValueError('Replay has no frame for this turn')
        if any(message.text != frame.message for message, frame in zip(messages, self.frames)):
            raise ValueError('Replay conversation does not match')
        frame = self.frames[index]
        if frame.tokens > max_tokens:
            raise ValueError('Replay response exceeds remaining token budget')
        return ModelReply(extraction=frame.extraction, tokens=frame.tokens)
