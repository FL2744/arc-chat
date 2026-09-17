"""Conservative model-context accounting without provider-specific tokenizers."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


DEFAULT_CONTEXT_TOKENS = 131_072
DEFAULT_OUTPUT_RESERVE = 16_384
CHARS_PER_TOKEN_ESTIMATE = 3.2
MAX_TOOL_OUTPUT_CHARS = 32_000


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN_ESTIMATE) + 1) if text else 0


def truncate_text(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = max(1000, limit // 3)
    tail = max(1000, limit - head - 80)
    omitted = len(text) - head - tail
    return text[:head] + f"\n...[{omitted} characters omitted]...\n" + text[-tail:]


def _message_text(message: Mapping[str, Any]) -> str:
    parts = [str(message.get("role", "")), str(message.get("content") or "")]
    if message.get("tool_calls"):
        parts.append(str(message["tool_calls"]))
    if message.get("tool_call_id"):
        parts.append(str(message["tool_call_id"]))
    return "\n".join(parts)


def bounded_history(
    history: Iterable[Mapping[str, Any]],
    *,
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    output_reserve: int = DEFAULT_OUTPUT_RESERVE,
) -> list[dict[str, Any]]:
    """Keep the newest coherent history that fits a conservative token budget.

    Tool outputs are individually clipped. Assistant tool-call + tool-result
    pairs are kept together when trimming from the front.
    """

    budget = max(8_192, context_tokens - output_reserve)
    normalized: list[dict[str, Any]] = []
    for source in history:
        item = dict(source)
        if isinstance(item.get("content"), str):
            item["content"] = truncate_text(item["content"])
        normalized.append(item)

    selected: list[dict[str, Any]] = []
    used = 0
    index = len(normalized) - 1
    while index >= 0:
        group = [normalized[index]]
        # A tool result is semantically tied to the immediately preceding
        # assistant tool call. Retain or discard them together.
        if normalized[index].get("role") == "tool" and index > 0 and normalized[index - 1].get("role") == "assistant":
            group.insert(0, normalized[index - 1])
            index -= 1
        cost = sum(estimate_tokens(_message_text(item)) + 12 for item in group)
        if selected and used + cost > budget:
            break
        selected[0:0] = group
        used += cost
        index -= 1
    return selected

