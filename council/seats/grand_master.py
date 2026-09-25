"""The Grand Master -- "Master of the Order", Tier IV synthesis. Sees
everything: every Tier I lean, the full debate, every Prosecutor verdict,
the Reality Anchors, and each term's warnings. Uses the stronger model.

It explains the council's three positions -- it never moves them. Each
position is the weighted average of the seats' leans, computed before the
Grand Master is called, so a weak lean stays visibly weak however the
synthesis reads.

Hard rule on synthesis output: it must report the structure of
disagreement, not just a consensus number. Unanimity among correlated
agents is a warning sign, not a green light."""
from __future__ import annotations

from council.engine.aggregation import CouncilPosition
from council.engine.base_rate import RealityAnchor
from council.engine.horizons import TERM_NAMES, TERM_WINDOWS, TERMS
from council.engine.llm_client import LLMCallFailed, LLMClient, SchemaRetryExhausted
from council.engine.schemas import (
    DebateArgument,
    GrandMasterSynthesis,
    ProsecutorVerdict,
    Tier1Summary,
    format_debate_transcript,
    format_tier1_summaries,
    summarize_dissent,
)

_SYSTEM_PROMPT = """
You are the Grand Master, "Master of the Order" -- the final synthesiser on
a market-prediction council. The council has already reached a position on
three terms -- short (the next week), medium (the next 3 months) and long
(the next year and beyond) -- each the weighted average of every seat's
lean. You see those positions, every seat's leans, the full Bull/Bear
debate, every Prosecutor verdict, the Reality Anchors and each term's
warnings.

Your job is to EXPLAIN the positions to someone deciding what to do, not to
change them: for each term, say what drives the lean and how much to trust
it. Be honest about weak leans -- a position barely off the middle is
close to a coin flip, and saying so is the most useful thing you can do.
Point out when terms disagree (e.g. bearish this week, bullish over the
year) and why.

Hard rule: report the STRUCTURE of disagreement, not just a consensus
number. Unanimity among seats that all leaned on the same correlated
evidence is a warning sign, not a green light -- your dissent_summary must
name who disagreed and on which terms (or note that agreement may be
inflated by correlated evidence). If correlated evidence was flagged,
correlated_evidence_warning must describe what it means for trust, not just
echo that it exists. Where the Prosecutor objected to a term, weigh that
objection in the term's note.

Write everything twice: once for an experienced investor (headline and the
short/medium/long notes) and once for someone new to investing (the plain_
fields). The plain versions say the same thing in everyday words -- "more
likely to go up", "a toss-up", "analysts expect higher profits" -- with no
jargon and no percentages, and never sound more certain than the expert
versions.
"""


def _format_position(term: str, position: CouncilPosition, warnings: list[str]) -> str:
    line = (
        f"- {TERM_NAMES[term]} ({TERM_WINDOWS[term]}): {position.lean_label} -- "
        f"{position.p_bullish:.1%} chance of rising, "
        f"{position.consensus_pct:.0f}% of leaning seats agree, "
        f"{position.seats_counted} seats counted"
    )
    if warnings:
        line += "\n    warnings: " + "; ".join(warnings)
    return line


class GrandMasterSeat:
    id = "grand_master"
    title = "Master of the Order"

    async def synthesize(
        self,
        *,
        tier1_summaries: list[Tier1Summary],
        debate_transcript: list[DebateArgument],
        prosecutor_verdicts: list[ProsecutorVerdict],
        positions: dict[str, CouncilPosition],
        warnings: dict[str, list[str]],
        reality_anchors: dict[str, RealityAnchor],
        correlated_evidence: list[str],
        llm_client: LLMClient,
        model: str,
        user_context: str | None = None,
    ) -> GrandMasterSynthesis | None:
        anchors = "\n".join(
            f"- {TERM_NAMES[t]}: the stock usually moves up to "
            f"{reality_anchors[t].max_plausible_move_pct}% over this term; it rose in "
            f"{reality_anchors[t].hit_rate_up if reality_anchors[t].hit_rate_up is not None else 'unknown'} "
            f"of past windows"
            + (
                f"; options imply a {reality_anchors[t].options_implied_move_pct}% move"
                if reality_anchors[t].options_implied_move_pct
                else ""
            )
            for t in TERMS
        )
        user_prompt = (
            (
                f"The user asked specifically: \"{user_context}\" -- address it directly in "
                "your reasoning, but stay grounded in the evidence below, not the question's "
                "framing.\n\n"
                if user_context
                else ""
            )
            + "Council positions (already computed -- explain them, don't change them):\n"
            + "\n".join(_format_position(t, positions[t], warnings.get(t, [])) for t in TERMS)
            + "\n\nWho leaned which way:\n"
            + "\n".join(f"- {TERM_NAMES[t]}: {summarize_dissent(tier1_summaries, t)}" for t in TERMS)
            + f"\n\nTier I leans:\n{format_tier1_summaries(tier1_summaries)}\n\n"
            f"Debate transcript:\n{format_debate_transcript(debate_transcript)}\n\n"
            f"Prosecutor findings: "
            f"{[f.description for pv in prosecutor_verdicts for f in pv.findings] or 'none'}\n\n"
            f"Reality Anchors:\n{anchors}\n\n"
            f"Correlated evidence: {correlated_evidence or 'none'}\n\n"
            "Write the synthesis."
        )
        try:
            return await llm_client.get_structured(
                seat_id=self.id,
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=GrandMasterSynthesis,
                fixture_name="grand_master",
            )
        except (SchemaRetryExhausted, LLMCallFailed):
            return None
