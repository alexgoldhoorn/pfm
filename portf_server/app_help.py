"""Search the web client's help text for the AI chat's ``get_app_help`` tool.

The text lives in ``web_client/js/help_text.js`` and is exported to
``portf_server/data/app_help.json`` by ``scripts/export_app_help.mjs``; a JS
test fails when the JSON is stale.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

HELP_JSON = Path(__file__).parent / "data" / "app_help.json"

# Longest page guide text returned in a search hit; a full guide is fetched
# with ``page=``.
_PAGE_SNIPPET_CHARS = 1200
_MAX_GLOSSARY = 6
_MAX_PAGES = 3
_MAX_METRICS = 5


@lru_cache(maxsize=1)
def load_app_help() -> dict:
    """Return the exported help document (cached for the process)."""
    return json.loads(HELP_JSON.read_text(encoding="utf-8"))


# Question words that would otherwise have to match every entry.
_STOPWORDS = frozenset(
    "a an and are about app can do does explain for how i in is it me mean "
    "means my of on or should the this to what when where which why with "
    "you your".split()
)


def _words(text: str) -> list[str]:
    """Lower-case search words without stopwords and single characters."""
    return [
        w
        for w in re.findall(r"[\w/€&-]+", text.lower())
        if len(w) > 1 and w not in _STOPWORDS
    ]


def _pattern(word: str) -> re.Pattern:
    """Match a word at a word start; words of up to 4 characters (IRR, TER,
    ROE) must also end there, with an optional plural s, so "irr" does not
    hit "irregular"."""
    tail = r"s?(?![\w])" if len(word) <= 4 else ""
    return re.compile(r"(?<![\w])" + re.escape(word) + tail)


def _score(patterns: list[re.Pattern], title: str, body: str, require_all: bool) -> int:
    """Title hits weigh three times a body hit. With ``require_all`` an entry
    missing any word scores 0."""
    title_l, body_l = title.lower(), body.lower()
    score = 0
    for pat in patterns:
        if pat.search(title_l):
            score += 3
        elif pat.search(body_l):
            score += 1
        elif require_all:
            return 0
    return score


def _snippet(text: str, words: list[str], limit: int) -> str:
    """Up to ``limit`` characters of ``text``, starting near the first hit."""
    if len(text) <= limit:
        return text
    low = text.lower()
    hits = [low.find(w) for w in words if low.find(w) >= 0]
    start = max(0, min(hits) - limit // 4) if hits else 0
    end = start + limit
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def search_app_help(query: Optional[str] = None, page: Optional[str] = None) -> dict:
    """Search the guide, glossary and metric help.

    Args:
        query: Words to look for; every word must match an entry.
        page: A page key; returns that page's full guide.

    Returns:
        A dict with matching ``glossary``, ``pages`` and ``metrics`` entries, the
        full guide for ``page``, or, with neither argument, an index of pages
        and glossary terms.
    """
    doc = load_app_help()
    if page:
        key = page.strip().lower()
        for p in doc["pages"]:
            if key in (p["key"].lower(), p["title"].lower()):
                return {"page": p}
        return {
            "error": f"no help page '{page}'",
            "pages": [{"key": p["key"], "title": p["title"]} for p in doc["pages"]],
        }

    words = _words(query or "")
    if not words:
        return {
            "pages": [{"key": p["key"], "title": p["title"]} for p in doc["pages"]],
            "glossary_terms": [g["term"] for g in doc["glossary"]],
            "hint": "Pass query= to search, or page=<key> for one page's guide.",
        }

    patterns = [_pattern(w) for w in words]

    def ranked(items, title_of, body_of, limit, require_all):
        scored = [
            (_score(patterns, title_of(i), body_of(i), require_all), n, i)
            for n, i in enumerate(items)
        ]
        hits = sorted((t for t in scored if t[0]), key=lambda t: (-t[0], t[1]))
        return [i for _, _, i in hits][:limit]

    def search(require_all: bool) -> tuple[list, list, list]:
        return (
            ranked(
                doc["glossary"],
                lambda g: g["term"],
                lambda g: g["definition"],
                _MAX_GLOSSARY,
                require_all,
            ),
            ranked(
                doc["pages"],
                lambda p: p["title"],
                lambda p: p["text"],
                _MAX_PAGES,
                require_all,
            ),
            ranked(
                doc["metrics"],
                lambda m: m["key"],
                lambda m: m["text"],
                _MAX_METRICS,
                require_all,
            ),
        )

    # Every word first; a question that matches nothing that way falls back
    # to entries holding any of its words, best match first.
    glossary, pages, metrics = search(require_all=True)
    partial = not (glossary or pages or metrics) and len(words) > 1
    if partial:
        glossary, pages, metrics = search(require_all=False)
    out: dict = {
        "query": query,
        "glossary": glossary,
        "pages": [
            {
                "key": p["key"],
                "title": p["title"],
                "text": _snippet(p["text"], words, _PAGE_SNIPPET_CHARS),
            }
            for p in pages
        ],
        "metrics": metrics,
    }
    if partial:
        out["partial_match"] = True
    if not (glossary or pages or metrics):
        out["note"] = (
            "Nothing in the app's help matches; try fewer or other words. If it is "
            "a general finance question, answer from general knowledge and say so."
        )
    return out
