"""Tests for ToolCapableLLMClient protocol and tool data model."""

from unittest.mock import MagicMock


def test_tool_definition_importable():
    from portf_manager.llm_client import ToolDefinition

    t = ToolDefinition(
        name="get_quote",
        description="Get live price for a symbol.",
        parameters=[
            {
                "name": "symbol",
                "type": "string",
                "description": "Ticker",
                "required": True,
            }
        ],
    )
    assert t.name == "get_quote"
    assert t.parameters[0]["name"] == "symbol"


def test_tool_call_request_has_optional_call_id():
    from portf_manager.llm_client import ToolCallRequest

    tc = ToolCallRequest(name="get_holdings", arguments={"symbol": "AAPL"})
    assert tc.call_id is None

    tc2 = ToolCallRequest(name="get_holdings", arguments={}, call_id="abc123")
    assert tc2.call_id == "abc123"


def test_tool_response_text_only():
    from portf_manager.llm_client import ToolResponse

    r = ToolResponse(text="Here is the answer.")
    assert r.text == "Here is the answer."
    assert r.tool_call is None


def test_tool_response_tool_call_only():
    from portf_manager.llm_client import ToolCallRequest, ToolResponse

    tc = ToolCallRequest(name="get_holdings", arguments={})
    r = ToolResponse(tool_call=tc)
    assert r.text is None
    assert r.tool_call.name == "get_holdings"


def test_mock_without_tool_methods_is_not_tool_capable():
    from portf_manager.llm_client import ToolCapableLLMClient

    plain = MagicMock(spec=["generate"])
    assert not isinstance(plain, ToolCapableLLMClient)


def test_mock_with_tool_methods_is_tool_capable():
    from portf_manager.llm_client import ToolCapableLLMClient

    # Must include 'generate' (from LLMClient parent) plus tool methods
    capable = MagicMock()
    capable.generate = MagicMock()
    capable.generate_with_tools = MagicMock()
    capable.complete_with_tool_result = MagicMock()
    assert isinstance(capable, ToolCapableLLMClient)
