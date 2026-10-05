"""Trace redaction (E20S1): agent traces are debuggable without being a
second copy of every document.

`redact_transcript` walks an AgentRunner transcript and bounds every string
payload: long document text is truncated in place, base64 content blobs are
dropped entirely, structure (call names, arguments, op kinds, stop reasons)
is kept verbatim. Redaction is lossy BY DESIGN — a trace answers "what did
the agent do", never "what did the document say".
"""

from __future__ import annotations

from typing import Any

REDACT_MAX_CHARS = 400
DROP_KEYS = {"content_base64"}


def redact_transcript(
    transcript: list[dict[str, Any]],
    max_chars: int = REDACT_MAX_CHARS,
) -> list[dict[str, Any]]:
    """Deep-copy a transcript with bounded strings and dropped blobs."""
    return [_redact(entry, max_chars) for entry in transcript]


def _redact(value: Any, max_chars: int) -> Any:
    if isinstance(value, dict):
        return {
            k: ("[dropped]" if k in DROP_KEYS else _redact(v, max_chars))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, max_chars) for item in value]
    if isinstance(value, str) and len(value) > max_chars:
        return value[:max_chars] + f"…[{len(value)} chars total]"
    return value
