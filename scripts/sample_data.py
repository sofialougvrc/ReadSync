#!/usr/bin/env python3
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.db import init_db, log_activity
from app.services.code_indexer import add_repository, index_repository
from app.services.extraction import run_extraction
from app.services.ingestion import store_paper
from app.services.matcher import run_matching


def make_sample_repo() -> Path:
    repo = ROOT / "sample_repo"
    repo.mkdir(exist_ok=True)
    (repo / "attention.py").write_text(
        '''
import math

def scaled_dot_product_attention(query, key, value):
    """Compute transformer-style attention weights and aggregate values."""
    scores = query @ key.T / math.sqrt(key.shape[-1])
    weights = softmax(scores)
    return weights @ value

def softmax(values):
    exp_values = [math.exp(v) for v in values]
    total = sum(exp_values)
    return [v / total for v in exp_values]
'''.strip(),
        encoding="utf-8",
    )
    (repo / "retrieval.js").write_text(
        '''
export function rankDocuments(queryEmbedding, documents) {
  return documents
    .map(doc => ({ doc, score: cosineSimilarity(queryEmbedding, doc.embedding) }))
    .sort((a, b) => b.score - a.score)
}

export function cosineSimilarity(a, b) {
  const dot = a.reduce((sum, value, index) => sum + value * b[index], 0)
  return dot / (Math.hypot(...a) * Math.hypot(...b))
}
'''.strip(),
        encoding="utf-8",
    )
    return repo


def main():
    init_db()
    paper_id = store_paper(
        {
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
            "year": 2017,
            "arxiv_id": "1706.03762",
            "categories": ["cs.CL", "cs.LG"],
            "abstract": "The Transformer replaces recurrent sequence modeling with attention mechanisms.",
        },
        """
        The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.
        We propose the Transformer, a model architecture relying entirely on attention mechanisms.
        Scaled dot-product attention uses queries, keys, and values. Multi-head attention allows the model to jointly attend to information from different representation subspaces.
        We evaluate on machine translation using BLEU and report strong quality with more parallelizable training.
        """,
        "sample",
        "https://arxiv.org/abs/1706.03762",
    )
    run_extraction(paper_id)
    repo_id = add_repository(str(make_sample_repo()))
    index_repository(repo_id)
    result = run_matching(paper_id)
    log_activity("sample", "Sample data loaded", "Attention paper plus local sample code repository.")
    print(f"Loaded sample paper #{paper_id}, sample repo #{repo_id}, and {result['matches_created']} candidate matches.")


if __name__ == "__main__":
    main()
