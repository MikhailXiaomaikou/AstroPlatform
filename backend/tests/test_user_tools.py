import pytest


def setup_function():
    from app.services._kv_store import use_memory_backend_for_testing
    from app.services.user_tools import _STORE

    use_memory_backend_for_testing()
    _STORE.clear_namespace()


def test_create_list_and_delete_user_tool_macro():
    from app.services.user_tools import (
        create_user_tool,
        delete_user_tool,
        list_user_tools,
    )

    tool = create_user_tool(
        scope="user:test-user",
        tool_id="quick_lookup",
        display_name="Quick lookup",
        description="Search a target in SIMBAD with a fixed radius.",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        steps=[{
            "tool_name": "search_objects",
            "input": {"query": "{{target}}", "sources": ["simbad"], "radius": 0.05},
        }],
    )

    assert tool["tool_id"] == "quick_lookup"
    assert list_user_tools("user:test-user")[0]["display_name"] == "Quick lookup"
    assert list_user_tools("user:other") == []
    assert delete_user_tool("user:test-user", "quick_lookup") is True
    assert list_user_tools("user:test-user") == []


def test_user_tool_rejects_raw_python_step():
    from app.services.user_tools import UserToolError, create_user_tool

    with pytest.raises(UserToolError, match="run_python"):
        create_user_tool(
            scope="user:test-user",
            tool_id="bad_python",
            display_name="Bad Python",
            description="This should not be accepted as a macro step.",
            input_schema={"type": "object", "properties": {}},
            steps=[{"tool_name": "run_python", "input": {"code": "print(1)"}}],
        )


@pytest.mark.asyncio
async def test_execute_user_tool_renders_arguments_and_uses_existing_tool_path(monkeypatch):
    from app.services.user_tools import create_user_tool, execute_user_tool

    create_user_tool(
        scope="user:test-user",
        tool_id="gaia_target",
        display_name="Gaia target",
        description="Run a saved Gaia object search for a named target.",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        steps=[{
            "tool_name": "search_objects",
            "input": {"query": "{{target}}", "sources": ["gaia"]},
        }],
    )

    calls = []

    async def fake_execute_tool(tool_name, tool_input, **kwargs):
        calls.append((tool_name, tool_input, kwargs))
        return {
            "success": True,
            "__tool_status__": "COMPLETED",
            "results": [{"name": tool_input["query"]}],
        }

    import app.services.ai_tools as ai_tools

    monkeypatch.setattr(ai_tools, "execute_tool", fake_execute_tool)

    result = await execute_user_tool(
        scope="user:test-user",
        tool_id="gaia_target",
        arguments={"target": "M31"},
        user_id="test-user",
        chat_session_id="chat-1",
        python_session_id="py-1",
    )

    assert result["__tool_status__"] == "COMPLETED"
    assert result["steps_run"] == 1
    assert calls[0][0] == "search_objects"
    assert calls[0][1] == {"query": "M31", "sources": ["gaia"]}
    assert calls[0][2]["user_id"] == "test-user"


@pytest.mark.asyncio
async def test_execute_user_tool_rejects_unknown_arguments():
    from app.services.user_tools import create_user_tool, execute_user_tool

    create_user_tool(
        scope="user:test-user",
        tool_id="strict_tool",
        display_name="Strict tool",
        description="A macro with strict input validation.",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        steps=[{"tool_name": "search_objects", "input": {"query": "{{target}}"}}],
    )

    result = await execute_user_tool(
        scope="user:test-user",
        tool_id="strict_tool",
        arguments={"target": "M31", "extra": "nope"},
    )

    assert result["__tool_status__"] == "FAILED"
    assert result["error_class"] == "invalid_user_tool_arguments"
