"""Public MCP response contracts; independent of transport and search internals."""

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["knowledge", "news", "web", "forum", "archive", "video", "film", "social"]
Tag = Literal["knowledge", "texts", "people", "small", "academic", "old"]


class Link(BaseModel):
    url: str
    title: str


class Answer(Link):
    text: str


class Definition(BaseModel):
    word: str
    url: str
    pos: str
    senses: list[str]
    synonyms: list[str]
    asked: bool = False


class Paper(BaseModel):
    authors: list[str]
    year: str
    journal: str
    pdf: str
    citation: str


class SearchHit(Link):
    rank: int
    snippet: str
    kind: Kind
    tags: list[Tag]
    category: str | None
    trust_tier: int | None
    found_in: list[str]
    cited_by_wikipedia: bool
    copies: list[Link]
    paper: Paper | None = None


class SearchFilters(BaseModel):
    kind: Kind | None
    tag: Tag | None
    domain: str | None


class RelatedQuery(BaseModel):
    query: str
    source: str


class SearchResponse(BaseModel):
    query: str
    read_as: str
    deep: bool
    results: list[SearchHit]
    returned: int
    offset: int
    next_offset: int | None
    matched_candidates: int
    has_more: bool
    filters: SearchFilters
    spelling: dict[str, str]
    did_you_mean: dict[str, str]
    answer: Answer | None
    definition: Definition | None
    related_queries: list[RelatedQuery]
    index_hits: dict[str, int]
    seconds: dict[str, float]
    note: str


class SourceResponse(BaseModel):
    url: str
    found: bool
    index: str | None = None
    text_kind: Literal["full_text", "abstract", "catalog_description"] | None = None
    title: str | None = None
    date: str | None = None
    authors: str | None = None
    year: str | None = None
    pdf: str | None = None
    text: str = ""
    offset: int = 0
    total_chars: int = 0
    next_offset: int | None = None
    provenance: dict[str, str] | None = Field(default=None, description="Imported corpus dataset, revision and source-record metadata")
    index_modified_at: str | None = Field(default=None, description="Database modification time; not source publication time")
    note: str


class TrustedSource(BaseModel):
    domain: str
    category: str
    trust_tier: int


class SourcesResponse(BaseModel):
    sources: list[TrustedSource]
    categories: list[str]
    note: str


class WordAnalysis(BaseModel):
    lemma: str
    pos: str
    family: str
    source: str


class AnalysisResponse(BaseModel):
    input: str
    word: str
    recognized: bool
    analyses: list[WordAnalysis]
    note: str


class Family(BaseModel):
    lemma: str
    family: str
    forms: list[str]
    members: list[str]
    forms_truncated: bool
    members_truncated: bool


class WordNode(BaseModel):
    word: str
    relationship: Literal["inflection", "preverb_or_verbal_noun", "other_preverb", "derived", "similar_meaning"]
    similarity: float
    corpus_count: int


class FamilyResponse(BaseModel):
    input: str
    word: str
    families: list[Family]
    graph_word: str | None
    neighbors: list[WordNode]
    links: list[tuple[str, str]]
    note: str


class DefinitionResponse(BaseModel):
    input: str
    word: str
    found: bool
    definition: Definition | None
    note: str


class DatedLink(Link):
    date: str | None


class WikiCitation(BaseModel):
    title: str
    count: int


class Repository(BaseModel):
    url: str
    name: str
    records: int


class SiteResponse(BaseModel):
    domain: str
    name: str
    category: str | None
    trust_tier: int | None
    small: bool
    indexed_pages: int
    latest_pages: list[DatedLink]
    wikipedia_citations: list[WikiCitation]
    citation_count: int
    archive_copies: list[DatedLink]
    archive_count: int
    repositories: list[Repository]
    links_in: list[str]
    links_out: list[str]
    similar_sites: list[str]
    note: str


class Passage(BaseModel):
    id: int
    title: str
    text: str
    index: str
    source_url: str


class PassagesResponse(BaseModel):
    url: str
    found: bool
    passages: list[Passage]
    after_id: int
    next_after_id: int | None
    index_modified_at: str | None
    note: str
