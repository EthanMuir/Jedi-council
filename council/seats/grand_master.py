"""The Grand Master -- "Master of the Order", Tier IV synthesis. Sees
everything: every Tier I verdict, the full debate, every Prosecutor
verdict, the Reality Anchor, and the Cost Auditor's result. Uses the
stronger model.

Hard rule on synthesis output: it must report the structure of
disagreement, not just a consensus number. Unanimity among correlated
agents is a warning sign, not a green light."""
from __future__ import annotations

from council.engine.base_rate import RealityAnchor
from council.engine.cost_auditor import CostAuditResult
from council.engine.llm_client import LLMClient, SchemaRetryExhausted
from council.engine.schemas import (
    DebateArgument,
    GrandMasterVerdict,
    ProsecutorVerdict,
    Tier1Summary,
    format_debate_transcript,
    format_tier1_summaries,
    summarize_dissent,
)

_SYSTEM_PROMPT = """
You are the Grand Master, "Master of the Order" -- the final synthesiser on
a market-prediction council. You see everything: every Tier I verdict, the
full Bull/Bear debate, every Prosecutor verdict, the Reality Anchor, and the
Cost Auditor's result.

Hard rule: you must report the STRUCTURE of disagreement, not just a
consensus number. Unanimity among seats that all leaned on the same
correlated evidence is a warning sign, not a green light -- your
dissent_summary must name who disagreed (or note that agreement may be
inflated by correlated evidence) rather than just restating the vote tally.
If correlated evidence was flagged, correlated_evidence_warning must
describe what it means for confidence, not just echo that it exists.

Your vote and confidence should track the weighted council vote already
computed (given to you), adjusted only for what the debate or Prosecutor
surfaced that a mechanical vote count couldn't see. You may NOT override a
Prosecutor veto: if any Prosecutor verdict this round vetoed, your vote
must be NO_CONVICTION regardless of how confident the underlying seats
were.
"""


class GrandMasterSeat:
    id = "grand_master"
    title = "Master of the Order"

    async def synthesize(
        self,
        *,
        tier1_summaries: list[Tier1Summary],
        debate_transcript: list[DebateArgument],
        prosecutor_verdicts: list[ProsecutorVerdict],
        weighted_vote_result: tuple[str, float, float],
        reality_anchor: RealityAnchor,
        cost_audit_result: CostAuditResult,
        correlated_evidence: list[str],
        llm_client: LLMClient,
        model: str,
    ) -> GrandMasterVerdict | None:
        vote, confidence, consensus_pct = weighted_vote_result
        vetoed = any(pv.veto for pv in prosecutor_verdicts)

        user_prompt = (
            f"Weighted council vote (Phase D, pre-synthesis): {vote} "
            f"(confidence={confidence}, consensus={consensus_pct}%)\n"
            f"Dissent map: {summarize_dissent(tier1_summaries)}\n\n"
            f"Tier I verdicts:\n{format_tier1_summaries(tier1_summaries)}\n\n"
            f"Debate transcript:\n{format_debate_transcript(debate_transcript)}\n\n"
            f"Prosecutor veto in effect: {vetoed}\n"
            f"Prosecutor findings: "
            f"{[f.description for pv in prosecutor_verdicts for f in pv.findings] or 'none'}\n\n"
            f"Reality Anchor: max plausible move {reality_anchor.max_plausible_move_pct}%, "
            f"hit-rate-up {reality_anchor.hit_rate_up}, options-implied move "
            f"{reality_anchor.options_implied_move_pct}%\n"
            f"Cost Auditor: edge {cost_audit_result.edge_pct}%, passed={cost_audit_result.passed}\n\n"
            f"Correlated evidence: {correlated_evidence or 'none'}\n\n"
            "Write the final synthesis."
        )
        try:
            return await llm_client.get_structured(
                seat_id=self.id,
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=GrandMasterVerdict,
                fixture_name="grand_master",
            )
        except SchemaRetryExhausted:
            return None
