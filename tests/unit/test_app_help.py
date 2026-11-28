"""get_app_help: search over the exported web-client help (app_help.json)."""

import json

from portf_server.app_help import load_app_help, search_app_help
from portf_server.chat_tools import execute_tool


def _terms(result: dict) -> list[str]:
    return [g["term"] for g in result["glossary"]]


def test_exported_help_has_glossary_pages_and_metrics():
    doc = load_app_help()
    assert len(doc["glossary"]) >= 80
    assert {"budget", "importexport", "research", "chat"} <= {
        p["key"] for p in doc["pages"]
    }
    assert any(m["key"] == "irr" for m in doc["metrics"])


def test_capex_question_finds_the_glossary_entry_first():
    result = search_app_help(query="What is capex?")
    assert _terms(result)[0] == "CapEx (capital expenditure)"
    assert "partial_match" not in result


def test_short_term_matches_whole_words_only():
    result = search_app_help(query="irr")
    assert _terms(result) == ["IRR"]
    # "irr" must not hit "irregular" in the budget guide.
    assert "budget" not in [p["key"] for p in result["pages"]]


def test_how_to_question_finds_the_page_guide():
    result = search_app_help(query="how do I import from Coinbase")
    assert [p["key"] for p in result["pages"]] == ["importexport"]
    assert "Coinbase" in result["pages"][0]["text"]


def test_partial_match_when_not_every_word_hits():
    result = search_app_help(query="capex zzzunknown")
    assert _terms(result)[0] == "CapEx (capital expenditure)"
    assert result["partial_match"] is True


def test_nothing_found_carries_a_note():
    result = search_app_help(query="zzzunknown")
    assert result["glossary"] == [] and result["pages"] == []
    assert "general knowledge" in result["note"]


def test_page_returns_the_full_guide_and_unknown_page_lists_keys():
    full = search_app_help(page="Budget")["page"]
    assert full["key"] == "budget"
    assert len(full["text"]) > 1200
    missing = search_app_help(page="nope")
    assert missing["error"] == "no help page 'nope'"
    assert "budget" in [p["key"] for p in missing["pages"]]


def test_no_arguments_returns_an_index():
    result = search_app_help()
    assert "CapEx (capital expenditure)" in result["glossary_terms"]
    assert "budget" in [p["key"] for p in result["pages"]]


def test_chat_tool_returns_json():
    out = json.loads(execute_tool("get_app_help", {"query": "traspaso"}, db=None))
    assert _terms(out)[0] == "Traspaso"
