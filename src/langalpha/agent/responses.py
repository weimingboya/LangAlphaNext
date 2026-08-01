from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    claim: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class DatasetReference(BaseModel):
    path: str = Field(pattern=r"^/workspace/\.langalpha/datasets/")
    source: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ResearchResult(BaseModel):
    summary: str = Field(min_length=1)
    evidence: list[EvidenceItem]
    datasets: list[DatasetReference] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
