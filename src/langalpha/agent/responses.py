from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    claim: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ResearchResult(BaseModel):
    summary: str = Field(min_length=1)
    evidence: list[EvidenceItem]
    dataset_paths: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReportResult(BaseModel):
    title: str = Field(min_length=1)
    executive_summary: str = Field(min_length=1)
    artifact_paths: list[str] = Field(default_factory=list)
    source_count: int = Field(ge=0)
