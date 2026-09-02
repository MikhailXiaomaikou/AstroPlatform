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
    """Backwards compat: when the assistant turn has no reasoning blocks and
    the caller did not declare thinking mode, the output must NOT contain a
    `reasoning_content` key.

    2026-09-02 live probe against deepseek-v4-pro with thinking enabled:
    an assistant tool_calls message with NO reasoning_content key -> HTTP 400
    ("must be passed back"); the same message with reasoning_content="" ->
    HTTP 200. So the earlier belief that DeepSeek rejects an empty string was
    wrong; what it rejects is the missing key. Thinking-mode callers pass
    thinking_mode=True and get the empty-string backfill (next tests)."""
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


def test_normalize_openai_messages_thinking_mode_backfills_synthesized_tool_turn() -> None:
    """Platform-synthesized tool turns (loop.py pre-LLM direct routes, registry
    and research-program steps) carry tool_use blocks but no reasoning block.
    DeepSeek thinking mode 400s on the next request unless the assistant
    tool_calls message carries a `reasoning_content` key; the live probe of
    2026-09-02 showed an empty string is accepted. Only the tool_calls branch
    is backfilled, and only when the caller declares thinking mode."""
    from app.ai.inference_router import _normalize_openai_messages

    messages = [
        {"role": "user", "content": "Quote the tension."},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_synth", "name": "compare_luminosity_distances",
             "input": {"comparison_mode": "h0_anchors"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_synth", "content": "{}"},
        ]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Plain text turn."},
        ]},
    ]

    thinking = _normalize_openai_messages(messages, thinking_mode=True)
    assistant = thinking[1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "compare_luminosity_distances"
    assert "reasoning_content" in assistant
    assert assistant["reasoning_content"] == ""
    # A text-only assistant turn is not a tool round; leave it untouched.
    assert "reasoning_content" not in thinking[3]

    plain = _normalize_openai_messages(messages, thinking_mode=False)
    assert "reasoning_content" not in plain[1]
    assert "reasoning_content" not in plain[3]


def test_synthesized_direct_route_turn_survives_deepseek_thinking_normalization(
    monkeypatch,
) -> None:
    """Exercise the real channel: the H0-anchor direct route synthesizes the
    first assistant turn without calling the model; the next model call must
    see a thinking-mode-valid history. The tool result deliberately lacks
    success=True so the loop does not also synthesize the final answer and
    the fake model is reached."""
    import asyncio

    import app.api.chat as chat_module
    from app.ai.inference_router import _normalize_openai_messages

    captured: dict = {}

    async def fake_llm(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {
            "content": "The registered comparison could not be completed.",
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(tool_calls, *_args, **_kwargs):
        return [
            {
                **call,
                "tool": call["name"],
                "result": {
                    "success": False,
                    "comparison_mode": "h0_anchors",
                    "__tool_status__": "FAILED",
                    "error": "registry unavailable in this test",
                },
            }
            for call in tool_calls
        ]

    monkeypatch.setattr(chat_module, "_llm_messages_create", fake_llm)
    monkeypatch.setattr(chat_module, "_execute_tool_calls", fake_exec)
    asyncio.run(
        chat_module._run_agent_loop(
            system="test cosmology system",
            messages=[{
                "role": "user",
                "content": (
                    "Quote the Hubble tension between Planck 2018 and "
                    "Riess 2022 SH0ES using compare_luminosity_distances."
                ),
            }],
            tools=[{"name": "compare_luminosity_distances", "input_schema": {}}],
            provider_api_keys={},
            agent_name="blind_test",
            python_session_id="deepseek-synthesized-turn-test",
        )
    )

    assert "messages" in captured, "the model was never reached"
    normalized = _normalize_openai_messages(captured["messages"], thinking_mode=True)
    tool_turns = [m for m in normalized if m.get("role") == "assistant" and m.get("tool_calls")]
    assert tool_turns, "expected the synthesized direct-route tool turn in the history"
    for turn in tool_turns:
        assert "reasoning_content" in turn, turn
