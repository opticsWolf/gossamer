"""Output-content budget enforcement for the toolbox.

Extracted from ``agent_tools.py`` during the composition split. Holds the
token/character truncation and JSON-fitting helpers that keep a research or
inspection result inside the configured output budget. Reads all
configuration through ``self._tb`` (the toolbox), mirroring SearchService /
FetchService / DocumentExtractor.
"""

import copy
import json
import logging
from typing import Optional

from stitch_web_researcher.models import _JSON_FIT_FLOOR
from stitch_web_researcher.structured_parser import ParsedDocumentPayload
from stitch_web_researcher.token_budget import count_tokens, truncate_to_tokens

logger = logging.getLogger(__name__)


class ContentBudget:
    """Output-content budget enforcement for the toolbox.

    Extracted from ``agent_tools.py`` during the composition split. Holds
    the token/character truncation and JSON-fitting helpers that keep a
    research or inspection result inside the configured output budget.
    Reads all configuration through ``self._tb`` (the toolbox), mirroring
    ``SearchService`` / ``FetchService`` / ``DocumentExtractor``.
    """

    def __init__(self, tb):
        self._tb = tb

    def _truncate(
        self, text: str, char_limit: int, token_limit: int = 0
    ) -> str:
        """
        Apply token-aware truncation.

        1. If *token_limit* > 0, truncate to that many tokens first.
        2. Then apply the character limit as a safety cap.

        This two-pass approach ensures we never exceed the token
        budget (primary constraint) while also staying below the
        character ceiling (fallback safety net).
        """
        if token_limit > 0:
            text = truncate_to_tokens(text, token_limit, self._tb.model_name)
        if len(text) > char_limit:
            text = text[:char_limit] + "\n\n... [truncated]"
        return text

    def _content_budget(self) -> tuple[int, int]:
        """(markdown_chars, markdown_tokens) budget with a links reserve.

        A fraction of the output budget (``link_budget_ratio``) is held back
        for the follow-up link list and the JSON envelope so that budget
        enforcement in ``_build_inspection_result`` always has room to keep
        at least some links on content-rich pages (C1: previously the
        markdown was truncated to exactly the envelope budget, leaving zero
        room for links, which were then all dropped).
        """
        keep = 1.0 - self._tb.link_budget_ratio
        chars = int(self._tb.max_markdown_chars * keep)
        tokens = int(self._tb.max_tokens * keep) if self._tb.max_tokens > 0 else 0
        return chars, tokens

    @staticmethod
    def _shrink_parsed_payload(payload_json: str, budget: Optional[int]) -> str:
        """Re-serialize a ParsedDocumentPayload with page text capped.

        ``budget`` of ``None`` returns the payload unchanged. The bulk of a
        parsed document is ``pages[].raw_text`` / ``pages[].markdown``, so
        those are what shrink; metadata, links and tables are small and stay
        intact because they are what the model navigates by.
        """
        if budget is None:
            return payload_json
        payload = ParsedDocumentPayload.model_validate_json(payload_json)
        for page in payload.pages:
            if len(page.raw_text) > budget:
                page.raw_text = page.raw_text[:budget] + "\n\n... [truncated]"
            if len(page.markdown) > budget:
                page.markdown = page.markdown[:budget] + "\n\n... [truncated]"
        return payload.to_json()

    @staticmethod
    def _shrink_research(result: dict, budget: Optional[int]) -> str:
        """Serialize a research result with per-source content capped.

        Shrinks each source's markdown first; if the budget is tight enough
        that even trimmed sources do not fit, whole sources are dropped from
        the tail and ``sources_omitted`` records how many, so the model can
        tell a short answer from a truncated one.
        """
        out = copy.deepcopy(result)
        if budget is None:
            return json.dumps(out, indent=2)
        for source in out.get("sources", []):
            page = source.get("result")
            if isinstance(page, dict):
                md = page.get("markdown")
                if isinstance(md, str) and len(md) > budget:
                    page["markdown"] = md[:budget] + "\n\n... [truncated]"
                if isinstance(page.get("follow_up_links"), list):
                    page["follow_up_links"] = page["follow_up_links"][:5]
            snippet = source.get("snippet")
            if isinstance(snippet, str) and len(snippet) > budget:
                source["snippet"] = snippet[:budget] + "..."
        # A very small budget means even trimmed sources will not all fit;
        # drop from the tail rather than emit a cut document.
        keep = max(1, budget // 120)
        if len(out.get("sources", [])) > keep:
            out["sources_omitted"] = len(out["sources"]) - keep
            out["sources"] = out["sources"][:keep]
        return json.dumps(out, indent=2)

    def _json_fits(self, text: str, char_limit: int, token_limit: int) -> bool:
        """True when *text* is inside both budgets."""
        if char_limit and len(text) > char_limit:
            return False
        if token_limit and count_tokens(text, self._tb.model_name) > token_limit:
            return False
        return True

    def _fit_json(
        self,
        build,
        char_limit: int,
        token_limit: int,
        overflow: dict,
    ) -> str:
        """Shrink a payload's text fields until its *serialized* form fits.

        ``build(budget)`` must return the payload serialized with every large
        text field truncated to ``budget`` characters (``None`` meaning no
        per-field cap).

        Cutting the serialized JSON instead — which is what ``_truncate`` does,
        and what these paths used to do — yields an unparseable payload, which
        is the LLM's entire reason for calling the tool. So the budget is
        applied to the *content* before serialization, and a payload that still
        will not fit is replaced by the small, valid ``overflow`` envelope
        rather than a cut. The invariant is absolute: this returns JSON.
        """
        out = build(None)
        if self._json_fits(out, char_limit, token_limit):
            return out

        budget = char_limit if char_limit > 0 else len(out)
        while budget > _JSON_FIT_FLOOR:
            budget //= 2
            out = build(budget)
            if self._json_fits(out, char_limit, token_limit):
                return out

        logger.warning(
            "Payload could not be shrunk into the output budget; "
            "returning an overflow envelope instead of invalid JSON"
        )
        return json.dumps(overflow, indent=2)
