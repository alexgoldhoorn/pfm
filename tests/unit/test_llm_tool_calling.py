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


class TestAnthropicToolCalling:
    def _make_client(self):
        from portf_manager.llm_client import AnthropicLLMClient

        return AnthropicLLMClient(api_key="test_key")

    def test_anthropic_satisfies_tool_capable_protocol(self):
        from portf_manager.llm_client import ToolCapableLLMClient

        client = self._make_client()
        assert isinstance(client, ToolCapableLLMClient)

    def test_generate_with_tools_returns_tool_call(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [
            ToolDefinition(
                name="get_holdings",
                description="Get open positions.",
                parameters=[],
            )
        ]
        messages = [{"role": "user", "content": "What are my holdings?"}]

        fake_block = MagicMock()
        fake_block.type = "tool_use"
        fake_block.name = "get_holdings"
        fake_block.input = {}
        fake_block.id = "toolu_123"

        fake_msg = MagicMock()
        fake_msg.content = [fake_block]

        with patch("anthropic.Anthropic") as mock_sdk:
            mock_sdk.return_value.messages.create.return_value = fake_msg
            response = client.generate_with_tools(messages, tools)

        assert response.tool_call is not None
        assert response.tool_call.name == "get_holdings"
        assert response.tool_call.call_id == "toolu_123"
        assert response.text is None

    def test_generate_with_tools_returns_text_when_no_tool(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_holdings", description=".", parameters=[])]
        messages = [{"role": "user", "content": "Hello"}]

        fake_block = MagicMock()
        fake_block.type = "text"
        fake_block.text = "Hi there!"

        fake_msg = MagicMock()
        fake_msg.content = [fake_block]

        with patch("anthropic.Anthropic") as mock_sdk:
            mock_sdk.return_value.messages.create.return_value = fake_msg
            response = client.generate_with_tools(messages, tools)

        assert response.text == "Hi there!"
        assert response.tool_call is None

    def test_complete_with_tool_result_returns_string(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolCallRequest

        client = self._make_client()
        messages = [{"role": "user", "content": "What are my holdings?"}]
        tool_call = ToolCallRequest(
            name="get_holdings", arguments={}, call_id="toolu_123"
        )

        fake_block = MagicMock()
        fake_block.type = "text"
        fake_block.text = "You hold 10 AAPL."

        fake_msg = MagicMock()
        fake_msg.content = [fake_block]

        with patch("anthropic.Anthropic") as mock_sdk:
            mock_sdk.return_value.messages.create.return_value = fake_msg
            result = client.complete_with_tool_result(
                messages,
                tool_call,
                '{"holdings": [{"symbol": "AAPL", "quantity": 10}]}',
            )

        assert result == "You hold 10 AAPL."
