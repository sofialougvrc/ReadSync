from ..db import connect, dumps, log_activity, log_error, utcnow
from .ingestion import BAD_CONTENT_MARKERS, MIN_EXTRACTED_TEXT_CHARS
from .llm import extract_with_ollama


def clear_existing_extraction(con, paper_id: int) -> None:
    for table in ("concepts", "algorithms", "code_patterns", "paper_items"):
        con.execute(f"DELETE FROM {table} WHERE paper_id = ?", (paper_id,))


def _assert_extractable_paper(paper: dict) -> None:
    raw_text = (paper["raw_text"] or "").strip()
    lowered = raw_text.lower()
    if any(marker in lowered for marker in BAD_CONTENT_MARKERS):
        raise ValueError("This record contains an access-check page instead of readable paper text. Re-ingest it from a PDF, arXiv link, DOI metadata source, or accessible article URL.")
    if len(raw_text) < MIN_EXTRACTED_TEXT_CHARS:
        raise ValueError("This record does not contain enough readable text for structured extraction. Re-ingest with a PDF, arXiv URL, DOI, BibTeX abstract, or accessible article text.")


def run_extraction(paper_id: int) -> dict:
    try:
        with connect() as con:
            paper = con.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not paper:
                raise ValueError("Paper not found.")
            _assert_extractable_paper(dict(paper))
            con.execute("UPDATE papers SET status = 'extracting', updated_at = ? WHERE id = ?", (utcnow(), paper_id))
        extraction = extract_with_ollama(paper["title"], paper["raw_text"], paper_id)
        with connect() as con:
            clear_existing_extraction(con, paper_id)
            for item in extraction.concepts:
                con.execute(
                    "INSERT INTO concepts(paper_id, name, description, type_tag, confidence) VALUES (?, ?, ?, ?, ?)",
                    (paper_id, item.name, item.description, item.type_tag, item.confidence),
                )
            for item in extraction.algorithms:
                con.execute(
                    "INSERT INTO algorithms(paper_id, name, description, pseudocode, confidence) VALUES (?, ?, ?, ?, ?)",
                    (paper_id, item.name, item.description, item.pseudocode, item.confidence),
                )
            for item in extraction.code_patterns:
                con.execute(
                    "INSERT INTO code_patterns(paper_id, name, description, language, confidence) VALUES (?, ?, ?, ?, ?)",
                    (paper_id, item.name, item.description, item.language, item.confidence),
                )
            for kind, values in (
                ("dataset", extraction.datasets),
                ("metric", extraction.evaluation_metrics),
                ("limitation", extraction.stated_limitations),
                ("citation", extraction.citations),
            ):
                for value in values:
                    con.execute(
                        "INSERT INTO paper_items(paper_id, kind, label, detail, confidence) VALUES (?, ?, ?, ?, ?)",
                        (paper_id, kind, str(value), "", 0.72),
                    )
            con.execute("UPDATE papers SET abstract = COALESCE(NULLIF(abstract, ''), ?), status = 'extracted', extraction_error = '', updated_at = ? WHERE id = ?", (extraction.core_contribution, utcnow(), paper_id))
        log_activity("extract", "Structured extraction complete", paper["title"], "paper", paper_id)
        return extraction.model_dump()
    except Exception as exc:
        log_error("extraction", str(exc), {"paper_id": paper_id}, paper_id)
        with connect() as con:
            clear_existing_extraction(con, paper_id)
            con.execute("DELETE FROM matches WHERE paper_id = ?", (paper_id,))
            con.execute("UPDATE papers SET status = 'error', extraction_error = ?, updated_at = ? WHERE id = ?", (str(exc), utcnow(), paper_id))
        raise
