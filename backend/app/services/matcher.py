import json
import re
import urllib.request
from functools import lru_cache

import numpy as np

from ..config import settings
from ..db import all_rows, connect, log_activity, log_error, setting_value, utcnow
from .embeddings import cosine, embed_text, embedding_from_json, jaccard, keyword_fingerprint


@lru_cache(maxsize=1)
def _provider_available() -> bool:
    provider = setting_value("llm_provider", settings.llm_provider).strip().lower()
    if provider == "openrouter":
        return bool(settings.openrouter_api_key.strip())
    try:
        endpoint = setting_value("ollama_endpoint", settings.ollama_endpoint).rstrip("/")
        request = urllib.request.Request(f"{endpoint}/api/tags", method="GET")
        with urllib.request.urlopen(request, timeout=0.75):
            return True
    except Exception:
        return False


def _faiss_candidates(idea_embedding: list[float], chunks: list[dict], top_k: int) -> set[int]:
    try:
        import faiss
    except Exception:
        return set()
    vectors = []
    chunk_ids = []
    for chunk in chunks:
        vector = embedding_from_json(chunk.get("embedding_json"))
        if vector:
            vectors.append(vector)
            chunk_ids.append(chunk["id"])
    if not vectors:
        return set()
    dim = min(len(idea_embedding), min(len(vector) for vector in vectors))
    matrix = np.array([vector[:dim] for vector in vectors], dtype="float32")
    query = np.array([idea_embedding[:dim]], dtype="float32")
    faiss.normalize_L2(matrix)
    faiss.normalize_L2(query)
    index = faiss.IndexFlatIP(dim)
    index.add(matrix)
    _, indices = index.search(query, min(top_k, len(chunk_ids)))
    return {chunk_ids[index_value] for index_value in indices[0] if index_value >= 0}


def _overlap_terms(concept: dict, chunk: dict, limit: int = 8) -> list[str]:
    idea_terms = keyword_fingerprint(" ".join([concept.get("name", ""), concept.get("description", ""), concept.get("pseudocode", "")]))
    code_terms = keyword_fingerprint(" ".join([chunk.get("signature", ""), chunk.get("docstring", ""), chunk.get("structural_text", ""), chunk.get("body", "")[:1200]]))
    terms = sorted(idea_terms & code_terms)
    return terms[:limit]


def _heuristic_judge(concept: dict, chunk: dict, semantic: float, structural: float) -> tuple[str, str, float]:
    combined = semantic * 0.7 + structural * 0.3
    classification = "already_implemented" if combined >= 0.62 else "partial" if combined >= 0.42 else "unapplied"
    terms = _overlap_terms(concept, chunk)
    idea_name = concept.get("name", "the extracted idea")
    location = f"{chunk.get('module_path')}:{chunk.get('start_line')}-{chunk.get('end_line')}"
    signature = re.sub(r"\s+", " ", chunk.get("signature", "")).strip()[:140]
    if classification == "already_implemented":
        reason = (
            f"ReadSync found strong textual and structural evidence that '{idea_name}' is implemented near {location}. "
            f"The candidate chunk '{signature}' shares implementation terms {', '.join(terms) if terms else 'from the concept description'} and has high similarity scores "
            f"(semantic {semantic:.2f}, structural {structural:.2f}). Review the code to confirm the behavior, but this is a strong implementation candidate."
        )
    elif classification == "partial":
        reason = (
            f"'{idea_name}' partially overlaps with {location}, especially around {', '.join(terms) if terms else 'related naming and control flow'}. "
            f"The chunk '{signature}' appears to contain one supporting ingredient, helper, interface, or adjacent workflow, but the full paper concept is not obvious from this function alone "
            f"(semantic {semantic:.2f}, structural {structural:.2f})."
        )
    else:
        reason = (
            f"'{idea_name}' is currently best treated as unapplied for {location}. "
            f"The chunk '{signature}' was retrieved as a weak candidate, but the overlap {f'({', '.join(terms)})' if terms else 'is mostly semantic rather than operational'} does not show the paper's mechanism, estimator, evaluation routine, or system pattern in code "
            f"(semantic {semantic:.2f}, structural {structural:.2f})."
        )
    return classification, reason, min(0.95, max(0.35, combined))


def _judge_with_ollama(concept: dict, chunk: dict, semantic: float, structural: float, use_llm: bool = True) -> tuple[str, str, float]:
    if not use_llm or not _provider_available():
        return _heuristic_judge(concept, chunk, semantic, structural)
    prompt = f"""
Classify whether this code chunk already implements the research idea, partially overlaps with it, or does not implement it.
Return JSON: {{"classification":"already_implemented|partial|unapplied","reason":"specific paper-specific reason","confidence":0.0}}
The reason must name the research idea, the code location, and the concrete code evidence or missing mechanism. Do not use a generic sentence.

Research idea:
{concept.get('name')} - {concept.get('description')}

Code chunk:
{chunk.get('module_path')}:{chunk.get('start_line')}-{chunk.get('end_line')}
{chunk.get('signature')}
{chunk.get('docstring')}
{chunk.get('body', '')[:3500]}

Similarity scores: semantic={semantic:.3f}, structural={structural:.3f}
"""
    payload = {
        "model": setting_value("ollama_model", settings.ollama_model),
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.0},
    }
    try:
        request = urllib.request.Request(
            f"{setting_value('ollama_endpoint', settings.ollama_endpoint).rstrip('/')}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=18) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body.get("response", "")
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        classification = data.get("classification", "unapplied")
        if classification not in ("already_implemented", "partial", "unapplied"):
            classification = "unapplied"
        reason = str(data.get("reason") or "").strip()
        if len(reason.split()) < 12:
            return _heuristic_judge(concept, chunk, semantic, structural)
        return classification, reason, float(data.get("confidence", semantic))
    except Exception:
        return _heuristic_judge(concept, chunk, semantic, structural)


def _judge_with_openrouter(concept: dict, chunk: dict, semantic: float, structural: float, use_llm: bool = True) -> tuple[str, str, float]:
    if not use_llm or not _provider_available() or not settings.openrouter_api_key.strip():
        return _heuristic_judge(concept, chunk, semantic, structural)
    prompt = f"""
Classify whether this code chunk already implements the research idea, partially overlaps with it, or does not implement it.
Return JSON: {{"classification":"already_implemented|partial|unapplied","reason":"specific paper-specific reason","confidence":0.0}}
The reason must name the research idea, the code location, and the concrete code evidence or missing mechanism. Do not use a generic sentence.

Research idea:
{concept.get('name')} - {concept.get('description')}

Code chunk:
{chunk.get('module_path')}:{chunk.get('start_line')}-{chunk.get('end_line')}
{chunk.get('signature')}
{chunk.get('docstring')}
{chunk.get('body', '')[:3500]}

Similarity scores: semantic={semantic:.3f}, structural={structural:.3f}
"""
    payload = {
        "model": setting_value("openrouter_model", settings.openrouter_model),
        "messages": [
            {"role": "system", "content": "You are ReadSync, a research-to-code reviewer. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.openrouter_api_key.strip()}",
    }
    if settings.openrouter_referer:
        headers["HTTP-Referer"] = settings.openrouter_referer
    if settings.openrouter_title:
        headers["X-Title"] = settings.openrouter_title
    try:
        request = urllib.request.Request(
            f"{setting_value('openrouter_base_url', settings.openrouter_base_url).rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=18) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        classification = data.get("classification", "unapplied")
        if classification not in ("already_implemented", "partial", "unapplied"):
            classification = "unapplied"
        reason = str(data.get("reason") or "").strip()
        if len(reason.split()) < 12:
            return _heuristic_judge(concept, chunk, semantic, structural)
        return classification, reason, float(data.get("confidence", semantic))
    except Exception as exc:
        log_error("openrouter_match_judge", str(exc), {"concept": concept.get("name"), "chunk_id": chunk.get("id")})
        return _heuristic_judge(concept, chunk, semantic, structural)


def _judge_match(concept: dict, chunk: dict, semantic: float, structural: float, use_llm: bool = True) -> tuple[str, str, float]:
    provider = setting_value("llm_provider", settings.llm_provider).strip().lower()
    if provider == "openrouter":
        return _judge_with_openrouter(concept, chunk, semantic, structural, use_llm=use_llm)
    return _judge_with_ollama(concept, chunk, semantic, structural, use_llm=use_llm)


def run_matching(paper_id: int | None = None, top_k: int = 8, per_repo_k: int = 3) -> dict:
    paper_filter = "WHERE paper_id = ?" if paper_id else ""
    params = (paper_id,) if paper_id else ()
    concepts = all_rows(f"SELECT *, 'concept' as source_kind FROM concepts {paper_filter}", params)
    algorithms = all_rows(f"SELECT *, 'algorithm' as source_kind FROM algorithms {paper_filter}", params)
    ideas = concepts + algorithms
    chunks = all_rows("SELECT c.*, r.name as repo_name FROM code_chunks c JOIN repositories r ON r.id = c.repo_id")
    reviewed_filter = "WHERE paper_id = ? AND review_state IN ('accepted', 'rejected')" if paper_id else "WHERE review_state IN ('accepted', 'rejected')"
    reviewed_params = (paper_id,) if paper_id else ()
    reviewed = {
        (row.get("paper_id"), row.get("concept_id"), row.get("algorithm_id"), row.get("chunk_id"))
        for row in all_rows(f"SELECT paper_id, concept_id, algorithm_id, chunk_id FROM matches {reviewed_filter}", reviewed_params)
    }
    pending_rows = []
    seen = set()
    for idea in ideas:
        idea_text = "\n".join([idea.get("name", ""), idea.get("description", ""), idea.get("pseudocode", "")])
        idea_embedding = embed_text(idea_text)
        idea_fingerprint = keyword_fingerprint(idea_text)
        faiss_ids = _faiss_candidates(idea_embedding, chunks, max(top_k * 3, 24))
        scored = []
        for chunk in chunks:
            semantic = cosine(idea_embedding, embedding_from_json(chunk.get("embedding_json")))
            structural = jaccard(idea_fingerprint, keyword_fingerprint(chunk.get("structural_text") or chunk.get("body") or ""))
            faiss_boost = 0.04 if chunk["id"] in faiss_ids else 0
            combined = semantic * 0.72 + structural * 0.28 + faiss_boost
            if combined > 0.10 or chunk["id"] in faiss_ids:
                scored.append((combined, semantic, structural, chunk))
        selected = []
        selected_ids = set()
        for row in sorted(scored, key=lambda row: row[0], reverse=True)[:top_k]:
            selected.append(row)
            selected_ids.add(row[3]["id"])
        by_repo: dict[int, list] = {}
        for row in scored:
            by_repo.setdefault(row[3]["repo_id"], []).append(row)
        for repo_rows in by_repo.values():
            for row in sorted(repo_rows, key=lambda item: item[0], reverse=True)[:per_repo_k]:
                if row[3]["id"] not in selected_ids:
                    selected.append(row)
                    selected_ids.add(row[3]["id"])
        for rank, (_, semantic, structural, chunk) in enumerate(selected):
            key = (
                idea["paper_id"],
                idea["id"] if idea["source_kind"] == "concept" else None,
                idea["id"] if idea["source_kind"] == "algorithm" else None,
                chunk["id"],
            )
            if key in reviewed or key in seen:
                continue
            seen.add(key)
            use_llm = rank < 2 and (semantic >= 0.22 or structural >= 0.08)
            classification, reason, confidence = _judge_match(idea, chunk, semantic, structural, use_llm=use_llm)
            pending_rows.append((
                idea["paper_id"],
                idea["id"] if idea["source_kind"] == "concept" else None,
                idea["id"] if idea["source_kind"] == "algorithm" else None,
                chunk["id"],
                "semantic+structural",
                classification,
                reason,
                confidence,
                semantic,
                structural,
                utcnow(),
            ))
    with connect() as con:
        if paper_id:
            con.execute("DELETE FROM matches WHERE paper_id = ? AND review_state = 'pending'", (paper_id,))
        else:
            con.execute("DELETE FROM matches WHERE review_state = 'pending'")
        con.executemany(
            """
            INSERT INTO matches(paper_id, concept_id, algorithm_id, chunk_id, track, classification, reason, confidence, semantic_score, structural_score, review_state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            pending_rows,
        )
    created = len(pending_rows)
    log_activity("match", "Matching run complete", f"{created} candidate links created", "paper", paper_id)
    return {"matches_created": created, "ideas": len(ideas), "chunks": len(chunks)}
