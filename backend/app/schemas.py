from typing import Literal
from pydantic import BaseModel, Field


class ConceptOut(BaseModel):
    id: int | None = None
    name: str
    description: str
    type_tag: str
    confidence: float = Field(ge=0, le=1)


class AlgorithmOut(BaseModel):
    id: int | None = None
    name: str
    description: str
    pseudocode: str = ""
    confidence: float = Field(ge=0, le=1)


class CodePatternOut(BaseModel):
    id: int | None = None
    name: str
    description: str
    language: str = ""
    confidence: float = Field(ge=0, le=1)


class PaperExtraction(BaseModel):
    core_contribution: str
    concepts: list[ConceptOut] = Field(default_factory=list)
    algorithms: list[AlgorithmOut] = Field(default_factory=list)
    code_patterns: list[CodePatternOut] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    evaluation_metrics: list[str] = Field(default_factory=list)
    stated_limitations: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class UrlIngestRequest(BaseModel):
    text: str
    source_type: Literal["arxiv", "article", "doi"] = "arxiv"


class BibtexIngestRequest(BaseModel):
    bibtex: str


class RepositoryRequest(BaseModel):
    path: str


class MatchReviewRequest(BaseModel):
    review_state: Literal["pending", "accepted", "rejected"]


class NoteRequest(BaseModel):
    body: str


class SettingsRequest(BaseModel):
    llm_provider: Literal["ollama", "openrouter"] | None = None
    ollama_endpoint: str | None = None
    ollama_model: str | None = None
    openrouter_base_url: str | None = None
    openrouter_model: str | None = None
