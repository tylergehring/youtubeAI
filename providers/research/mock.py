"""Mock research provider: attaches fake evidence; claims marked UNVERIFIABLE stay uncertain."""

from __future__ import annotations

from core.models.content import Claim, ClaimStatus, ClaimVerification, Evidence
from core.providers import ResearchProvider, ResearchResult, Usage


class MockResearchProvider(ResearchProvider):
    name = "mock"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def verify_claim(self, claim: Claim, topic: str) -> ResearchResult:
        self.calls.append(claim.id)
        if "UNVERIFIABLE" in claim.text:
            verification = ClaimVerification(
                claim_id=claim.id,
                status=ClaimStatus.UNCERTAIN_CLAIM,
                confidence=0.3,
                disputed=True,
            )
        else:
            evidence = Evidence(
                claim_id=claim.id,
                source_url="https://example.com/mock-evidence",
                source_title="Mock evidence (no real research was performed)",
                source_type="mock",
                excerpt="",
            )
            verification = ClaimVerification(
                claim_id=claim.id,
                status=ClaimStatus.VERIFIED_FACT,
                confidence=0.95,
                evidence=[evidence],
            )
        return ResearchResult(verification, Usage())
