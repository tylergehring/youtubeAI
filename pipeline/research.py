"""Verify important claims through a research provider and record evidence and confidence."""

from __future__ import annotations

from core.models.content import (
    ClaimStatus,
    ClaimVerification,
    KnowledgeBase,
    ResearchReport,
)
from core.models.manifest import StageName
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main

NEEDS_VERIFICATION = {ClaimStatus.SOURCE_CLAIM, ClaimStatus.UNCERTAIN_CLAIM}


class ResearchStage(Stage):
    """Verify source claims with a research provider and attach evidence."""

    name = StageName.RESEARCH

    def provider_parts(self, ctx: StageContext) -> str:
        return ctx.providers.research.name

    def run(self, ctx: StageContext) -> StageResult:
        knowledge = ctx.load(artifacts.knowledge_json(ctx.paths), KnowledgeBase)
        provider = ctx.providers.research
        results: list[ClaimVerification] = []

        for claim in knowledge.claims:
            if claim.status not in NEEDS_VERIFICATION:
                results.append(
                    ClaimVerification(
                        claim_id=claim.id, status=claim.status, confidence=claim.confidence
                    )
                )
                continue
            ctx.log.info("Verifying claim %s via '%s'", claim.id, provider.name)
            outcome = provider.verify_claim(claim, knowledge.topic)
            ctx.record_usage(provider.name, "research", outcome.usage, note=f"claim {claim.id}")
            results.append(outcome.verification)

        flagged = [
            r.claim_id for r in results if r.disputed or r.status is ClaimStatus.UNCERTAIN_CLAIM
        ]
        if flagged:
            ctx.log.warning("Uncertain or disputed claims: %s", ", ".join(flagged))
        report = ResearchReport(provider=provider.name, results=results)
        return StageResult([ctx.save(artifacts.research_json(ctx.paths), report)])


main = make_main(ResearchStage)

if __name__ == "__main__":
    raise SystemExit(main())
