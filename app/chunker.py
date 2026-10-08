"""Document extraction, section parsing, and recursive layout-aware semantic chunking."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ChunkMetadata:
    doc_id: str
    chunk_id: str
    chunk_index: int
    total_chunks: int
    prev_chunk_id: str | None
    next_chunk_id: str | None
    title: str
    url: str
    category: str
    hospital_id: str
    section: str = ""
    checked_at: str = ""
    layout_type: str = "section"  # 'faq', 'paragraph', 'sentence', 'list', 'section', 'word_window'


@dataclass
class DocumentChunk:
    chunk_id: str
    text: str
    metadata: ChunkMetadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "metadata": asdict(self.metadata)
        }


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences using punctuation lookbehinds."""
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def split_faq_items(text: str) -> list[tuple[str, str]]:
    """Detect and extract FAQ Q&A units from text."""
    lines = text.splitlines()
    items: list[tuple[str, str]] = []
    curr_q: str | None = None
    curr_a_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        q_match = re.match(r'^(?:Q[:\.]|Question[:\.]|\d+[\.\)])\s*(.+)', stripped, re.IGNORECASE)
        is_q = False
        question_text = ""
        if q_match:
            candidate = q_match.group(1).strip()
            if '?' in candidate or re.match(
                r'^(what|how|where|when|who|why|is|can|do|should|if|whom|are|will|which)\b',
                candidate,
                re.IGNORECASE
            ):
                is_q = True
                question_text = candidate
        elif stripped.startswith(('Q:', 'Question:')):
            is_q = True
            question_text = re.sub(r'^(?:Q:|Question:)\s*', '', stripped, flags=re.IGNORECASE)

        if is_q:
            if curr_q and curr_a_lines:
                ans_text = '\n'.join(curr_a_lines).strip()
                if ans_text:
                    items.append((curr_q, ans_text))
            curr_q = question_text
            curr_a_lines = []
        elif curr_q is not None:
            ans_match = re.match(r'^(?:A[:\.]|Answer[:\.]\s*)(.*)', stripped, re.IGNORECASE)
            if ans_match:
                curr_a_lines.append(ans_match.group(1).strip())
            else:
                curr_a_lines.append(line)

    if curr_q and curr_a_lines:
        ans_text = '\n'.join(curr_a_lines).strip()
        if ans_text:
            items.append((curr_q, ans_text))

    return items


def split_list_items(text: str) -> list[str]:
    """Split text into bullet/list items."""
    lines = text.splitlines()
    items: list[str] = []
    curr_item_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if re.match(r'^(?:[-*•]|\d+[\.\)])\s+', stripped):
            if curr_item_lines:
                items.append('\n'.join(curr_item_lines).strip())
            curr_item_lines = [stripped]
        elif curr_item_lines:
            curr_item_lines.append(stripped)
        elif stripped:
            curr_item_lines.append(stripped)

    if curr_item_lines:
        items.append('\n'.join(curr_item_lines).strip())

    return items


def recursive_chunk_text(
    text: str,
    section_title: str,
    chunk_size: int = 150,
    overlap: int = 30,
    layout_type: str = "section"
) -> list[tuple[str, str, str]]:
    """Recursively breaks text down through layout boundaries:
    FAQ Q&A -> Paragraphs -> Lists -> Sentences -> Word Windows.
    Returns tuples of (section_title, chunk_text, layout_type).
    """
    text = text.strip()
    if not text:
        return []

    words = text.split()
    if len(words) <= chunk_size:
        return [(section_title, text, layout_type)]

    # 1. Atomic FAQ Handling: keep questions & answers bound together
    faqs = split_faq_items(text)
    if faqs:
        results: list[tuple[str, str, str]] = []
        for q, a in faqs:
            faq_text = f"Q: {q}\nA: {a}"
            faq_words = faq_text.split()
            if len(faq_words) <= chunk_size:
                results.append((section_title, faq_text, "faq"))
            else:
                sub_chunks = recursive_chunk_text(
                    a,
                    section_title=f"{section_title} > {q[:40]}",
                    chunk_size=max(chunk_size - len(q.split()) - 5, 30),
                    overlap=overlap,
                    layout_type="faq"
                )
                for st, sc, lt in sub_chunks:
                    results.append((st, f"Q: {q}\n[Answer]: {sc}", lt))
        if results:
            return results

    # 2. Paragraph Boundary Level
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if len(paragraphs) > 1:
        results = []
        current_chunk_paras: list[str] = []
        current_count = 0

        for p in paragraphs:
            p_words = len(p.split())
            if p_words > chunk_size:
                if current_chunk_paras:
                    results.append((section_title, "\n\n".join(current_chunk_paras), "paragraph"))
                    current_chunk_paras = []
                    current_count = 0
                results.extend(recursive_chunk_text(
                    p, section_title, chunk_size, overlap, layout_type="paragraph"
                ))
            elif current_count + p_words <= chunk_size:
                current_chunk_paras.append(p)
                current_count += p_words
            else:
                results.append((section_title, "\n\n".join(current_chunk_paras), "paragraph"))
                if p_words <= overlap and len(current_chunk_paras) > 1:
                    current_chunk_paras = [current_chunk_paras[-1], p]
                    current_count = len(current_chunk_paras[0].split()) + p_words
                else:
                    current_chunk_paras = [p]
                    current_count = p_words

        if current_chunk_paras:
            results.append((section_title, "\n\n".join(current_chunk_paras), "paragraph"))
        return results

    # 3. List Item Boundary Level
    list_items = split_list_items(text)
    if len(list_items) > 1:
        results = []
        current_items: list[str] = []
        current_count = 0

        for item in list_items:
            i_words = len(item.split())
            if i_words > chunk_size:
                if current_items:
                    results.append((section_title, "\n".join(current_items), "list"))
                    current_items = []
                    current_count = 0
                results.extend(recursive_chunk_text(
                    item, section_title, chunk_size, overlap, layout_type="list"
                ))
            elif current_count + i_words <= chunk_size:
                current_items.append(item)
                current_count += i_words
            else:
                results.append((section_title, "\n".join(current_items), "list"))
                current_items = [item]
                current_count = i_words

        if current_items:
            results.append((section_title, "\n".join(current_items), "list"))
        return results

    # 4. Sentence Boundary Level
    sentences = split_into_sentences(text)
    if len(sentences) > 1:
        results = []
        current_sentences: list[str] = []
        current_count = 0

        for s in sentences:
            s_words = len(s.split())
            if s_words > chunk_size:
                if current_sentences:
                    results.append((section_title, " ".join(current_sentences), "sentence"))
                    current_sentences = []
                    current_count = 0
                step = max(chunk_size - overlap, 20)
                w = s.split()
                for start in range(0, len(w), step):
                    piece = " ".join(w[start:start + chunk_size])
                    if len(piece.strip()) >= 30:
                        results.append((section_title, piece.strip(), "word_window"))
            elif current_count + s_words <= chunk_size:
                current_sentences.append(s)
                current_count += s_words
            else:
                results.append((section_title, " ".join(current_sentences), "sentence"))
                if current_sentences and len(current_sentences[-1].split()) <= overlap:
                    current_sentences = [current_sentences[-1], s]
                    current_count = len(current_sentences[0].split()) + s_words
                else:
                    current_sentences = [s]
                    current_count = s_words

        if current_sentences:
            results.append((section_title, " ".join(current_sentences), "sentence"))
        return results

    # 5. Fallback: single oversized sentence or unpunctuated block
    results = []
    step = max(chunk_size - overlap, 20)
    for start in range(0, len(words), step):
        piece = " ".join(words[start:start + chunk_size])
        if len(piece.strip()) >= 30:
            results.append((section_title, piece.strip(), "word_window"))
    return results


def extract_sections(text: str) -> list[tuple[str, str]]:
    """Split text into (section_title, section_text) pairs based on headings/structure."""
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    current_title = "Introduction"
    current_lines: list[str] = []

    for line in lines:
        heading_match = re.match(r'^(?:#{1,4}|(?:\d+\.))\s+(.+)', line.strip())
        if heading_match:
            if current_lines:
                sec_content = "\n".join(current_lines).strip()
                if sec_content:
                    sections.append((current_title, sec_content))
                current_lines = []
            current_title = heading_match.group(1).strip()
        else:
            current_lines.append(line)

    if current_lines:
        sec_content = "\n".join(current_lines).strip()
        if sec_content:
            sections.append((current_title, sec_content))

    if not sections:
        sections = [("General", text.strip())]

    return sections


def chunk_document(
    doc_id: str,
    title: str,
    text: str,
    category: str,
    hospital_id: str,
    url: str = "",
    checked_at: str = "",
    chunk_size: int = 150,
    overlap: int = 30
) -> list[DocumentChunk]:
    """Break a document into sequential chunks with recursive layout awareness and adjacency links."""
    sections = extract_sections(text)
    raw_chunks: list[tuple[str, str, str]] = []

    for sec_title, sec_text in sections:
        if not sec_text.strip():
            continue
        sub_chunks = recursive_chunk_text(
            sec_text,
            section_title=sec_title,
            chunk_size=chunk_size,
            overlap=overlap,
            layout_type="section"
        )
        raw_chunks.extend(sub_chunks)

    if not raw_chunks:
        raw_chunks = [("General", text.strip(), "general")]

    total = len(raw_chunks)
    document_chunks: list[DocumentChunk] = []

    for i, (sec_title, chunk_text, layout_type) in enumerate(raw_chunks):
        chunk_id = f"{doc_id}_c{i}"
        prev_id = f"{doc_id}_c{i - 1}" if i > 0 else None
        next_id = f"{doc_id}_c{i + 1}" if i < total - 1 else None

        meta = ChunkMetadata(
            doc_id=doc_id,
            chunk_id=chunk_id,
            chunk_index=i,
            total_chunks=total,
            prev_chunk_id=prev_id,
            next_chunk_id=next_id,
            title=title,
            url=url,
            category=category,
            hospital_id=hospital_id,
            section=sec_title,
            checked_at=checked_at,
            layout_type=layout_type
        )
        document_chunks.append(DocumentChunk(chunk_id=chunk_id, text=chunk_text, metadata=meta))

    return document_chunks


# Alias for explicit recursive layout chunking
recursive_layout_chunk_document = chunk_document
