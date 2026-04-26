"""PART Z C6 — DeepSeek thinking-mode `reasoning_content` round-trip.

DeepSeek's thinking models (deepseek-reasoner / V3-thinking) return a
`reasoning_content` field alongside `content`. The contract is that the
NEXT request must echo the previous turn's `reasoning_content` back,
otherwise DeepSeek 400s with:
    "The reasoning_content in the thinking mode must be passed back to
    the API."

Today's audit caught the regression: chat.py was constructing the
assistant turn from `response["content"]` + `tool_calls` only, dropping
reasoning_content on the floor. The agent loop's second iteration
hit the 400.

Locks in the round-trip:
- inference_router.OpenAICompatibleBackend.complete: surfaces
  reasoning_content on the response dict.
- chat.py agent loop: stores it as a `{type: "reasoning_content"}` block
  on the assistant turn so it's transparent to providers that don't use
  it.
- _normalize_openai_messages: rebuilds the OpenAI-style dict with the
  top-level `reasoning_content` key when the assistant turn has those
  blocks, regardless of whether tool_use is also present.
"""

from __future__ import annotations


def test_normalize_openai_messages_carries_reasoning_with_tool_use() -> None:
    from app.ai.inference_router import _normalize_openai_messages

    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": [
            {"type": "reasoning_content", "text": "step 1 then step 2"},
            {"type": "text", "text": "Calling the tool now."},
            {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "result"},
        ]},
    ]
    out = _normalize_openai_messages(messages)

    # First user message — no change
    assert out[0] == {"role": "user", "content": "Hi"}

    # Assistant: reasoning_content survives + tool_calls intact
    assistant = out[1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "Calling the tool now."
    assert assistant["reasoning_content"] == "step 1 then step 2"
    assert len(assistant["tool_calls"]) == 1
    assert assistant["tool_calls"][0]["function"]["name"] == "search"

    # Tool result rewritten as OpenAI tool message
    assert out[2]["role"] == "tool"
    assert out[2]["tool_call_id"] == "tu_1"
    assert out[2]["content"] == "result"


def test_normalize_openai_messages_carries_reasoning_without_tool_use() -> None:
    """Reasoning + text only (final answer turn) — also must carry."""
    from app.ai.inference_router import _normalize_openai_messages

    messages = [
        {"role": "assistant", "content": [
            {"type": "reasoning_content", "text": "weighed two options"},
            {"type": "text", "text": "Final answer."},
        ]}
    ]
    out = _normalize_openai_messages(messages)
    assert out[0]["role"] == "assistant"
    assert out[0]["content"] == "Final answer."
    assert out[0]["reasoning_content"] == "weighed two options"
    # NOT inside tool_calls because there are no tool_uses
    assert "tool_calls" not in out[0]


def test_normalize_openai_messages_assistant_without_reasoning_unchanged() -> None:
    """Backwards compat: when the assistant turn has no reasoning blocks,
    the output must NOT contain a `reasoning_content` key (DeepSeek
    rejects empty / null reasoning_content payloads on later turns)."""
    from app.ai.inference_router import _normalize_openai_messages

    messages = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "Just text."},
        ]}
    ]
    out = _normalize_openai_messages(messages)
    assert "reasoning_content" not in out[0]


def test_normalize_openai_messages_user_message_never_carries_reasoning() -> None:
    """Sanity: the reasoning_content key only attaches to assistant turns."""
    from app.ai.inference_router import _normalize_openai_messages

    messages = [
        {"role": "user", "content": [
            {"type": "reasoning_content", "text": "should be dropped"},
            {"type": "text", "text": "user prose"},
        ]}
    ]
    out = _normalize_openai_messages(messages)
    assert "reasoning_content" not in out[0]
    assert out[0]["content"] == "user prose"
