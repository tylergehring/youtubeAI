"""Pass-through research: performs NO verification and leaves every claim attributed to its source.

Use this until a real research provider exists, so downstream stages never mistake an
unchecked claim for a verified fact (the mock provider fabricates evidence for testing).
"""

from __future__ import annotations

from core.models.content import Claim, ClaimVerification
from core.providers import ResearchProvider, ResearchResult, Usage


class PassthroughResearchProvider(ResearchProvider):
    name = "passthrough"

    def verify_claim(self, claim: Claim, topic: str) -> ResearchResult:
        return ResearchResult(
            ClaimVerification(claim_id=claim.id, status=claim.status, confidence=claim.confidence),
            Usage(requests=0),
        )
