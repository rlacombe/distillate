"""AI-powered paper summarization using Claude."""

import logging
import re
from typing import List, Optional, Tuple

from distillate import config

log = logging.getLogger(__name__)


def summarize_read_paper(
    title: str,
    abstract: str = "",
    key_learnings: Optional[List[str]] = None,
    reader_notes: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Generate summaries for a read paper.

    Returns (summary, one_liner):
      - summary: paragraph summarizing the paper's content and key ideas
      - one_liner: one tight sentence explaining why this paper matters,
        understandable by a non-specialist. Used as blockquote and in reading log.

    If ``reader_notes`` are provided (OCR'd handwritten margin notes),
    the summary is personalized to reflect what the reader found notable.
    """
    if not config.ANTHROPIC_API_KEY:
        return _fallback_read(title, abstract, key_learnings)

    # Need at least an abstract or key learnings to generate a summary
    if not abstract and not key_learnings:
        return _fallback_read(title, abstract, key_learnings)

    context_parts = []
    if abstract:
        context_parts.append(f"Abstract: {abstract}")
    if key_learnings:
        context_parts.append("Key takeaways:\n" + "\n".join(f"- {item}" for item in key_learnings))
    if reader_notes:
        context_parts.append(
            "Reader's handwritten margin notes:\n"
            + "\n".join(f"- {n}" for n in reader_notes)
        )
    context = "\n\n".join(context_parts)

    notes_instruction = ""
    if reader_notes:
        notes_instruction = (
            " The reader left margin notes while reading — let those "
            "guide which aspects you emphasize in the summary."
        )

    prompt = (
        f"You are summarizing a research paper for a personal reading log.\n\n"
        f"Paper: {title}\n\n{context}\n\n"
        f"Provide two things, separated by the exact line '---':\n\n"
        f"1. A paragraph (3-5 sentences) that a well-educated non-specialist "
        f"can understand without having read the paper. Structure it as: "
        f"(a) the problem or gap being addressed, (b) the approach or key idea, "
        f"(c) the main result or finding. Expand every acronym and technical "
        f"term on first use (e.g. 'linear mixed models (LMMs)'). Include "
        f"specific numbers, comparisons, or benchmarks when available. State "
        f"ideas directly as fact — never start with 'this paper' or 'the "
        f"authors'.{notes_instruction}\n\n"
        f"2. ONE or TWO sentences (max 280 characters) that answer: why does "
        f"this work matter? What changes because of it? What's the key "
        f"argument or insight? Stay at the level of the big idea — do NOT "
        f"drop into specific technical details or examples. The sentence "
        f"must be fully self-contained: no pronouns or demonstratives that "
        f"refer back to the paragraph (no 'this', 'these', 'such', 'it'). "
        f"No acronyms. A reader seeing ONLY this sentence must understand "
        f"it.\n\n"
        f"Format:\n[paragraph]\n---\n[one or two sentences]"
    )

    result = _call_claude(prompt, model=config.CLAUDE_SMART_MODEL)
    if result and "---" in result:
        parts = result.split("---", 1)
        return parts[0].strip(), parts[1].strip()

    if result:
        sentences = result.split(". ")
        one_liner = sentences[0].strip()
        if not one_liner.endswith("."):
            one_liner += "."
        return result, one_liner

    return _fallback_read(title, abstract, key_learnings)


def extract_insights(
    title: str,
    highlights: Optional[List[str]] = None,
    abstract: str = "",
    reader_notes: Optional[List[str]] = None,
) -> List[str]:
    """Extract key learnings from a paper's highlights.

    Returns a list of short bullet-point strings, ending with a
    'so what' bullet explaining why this work matters.

    If ``reader_notes`` are provided (OCR'd handwritten margin notes),
    they are included as context so the insights reflect what the reader
    found most interesting.
    """
    if not config.ANTHROPIC_API_KEY:
        return []

    context_parts = []
    if highlights:
        context_parts.append("Highlights:\n" + "\n".join(f"- {h}" for h in highlights))
    if abstract:
        context_parts.append(f"Abstract: {abstract}")
    if reader_notes:
        context_parts.append(
            "Reader's handwritten margin notes:\n"
            + "\n".join(f"- {n}" for n in reader_notes)
        )

    if not context_parts:
        return []

    context = "\n\n".join(context_parts)

    notes_instruction = ""
    if reader_notes:
        notes_instruction = (
            "The reader also wrote margin notes while reading. "
            "Prioritize insights that connect to what the reader "
            "found interesting or questioned.\n\n"
        )

    prompt = (
        f"From these highlights of \"{title}\":\n\n"
        f"{context}\n\n"
        f"{notes_instruction}"
        f"Return 4-6 bullet points:\n"
        f"- First 3-5: key facts, methods, or findings. Each one short "
        f"sentence (max 15 words). State facts directly, no filler. "
        f"Expand important acronyms on first use. Each bullet must add "
        f"distinct information — no two bullets should say the same thing.\n"
        f"- Last bullet: a 'So what?' — why this work matters, what it "
        f"enables, or what changes because of it. One sentence, max 20 words. "
        f"Be concrete and specific, not generic.\n\n"
        f"Format:\n"
        f"- fact one\n"
        f"- fact two\n"
        f"- So what: why it matters"
    )

    result = _call_claude(prompt, max_tokens=250, model=config.CLAUDE_FAST_MODEL)
    if not result:
        return []

    learnings = []
    for line in result.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^#{1,3}\s+", "", line)  # strip markdown headers
        cleaned = re.sub(r"^\d+[.)]\s*", "", cleaned)
        cleaned = re.sub(r"^[-*]\s*", "", cleaned)
        cleaned = re.sub(r"^\*\*.*?\*\*\s*", "", cleaned)
        cleaned = re.sub(r"^(LEARNINGS|SO WHAT|KEY POINTS?):?\s*", "", cleaned, flags=re.IGNORECASE)
        if cleaned and not re.match(r'^["\'"].+["\']\s*[-–—]\s*', cleaned):
            # Skip lines that are just paper titles: "Title" — subtitle
            learnings.append(cleaned)

    return learnings[:6]



def suggest_papers(
    unread: List[dict],
    recent_reads: List[dict],
) -> Optional[str]:
    """Ask Claude to pick the 3 best papers to read next.

    Returns the raw response text, or None on failure.
    """
    if not config.ANTHROPIC_API_KEY:
        return None

    # Build recent reads context
    reads_lines = []
    for p in recent_reads[:10]:
        tags = ", ".join(p.get("tags", []))
        summary = p.get("summary", "")
        engagement = p.get("engagement", 0)
        citations = p.get("citation_count", 0)
        eng_str = f", engagement:{engagement}%" if engagement else ""
        cite_str = f", {citations} citations" if citations else ""
        reads_lines.append(f"- [read{eng_str}{cite_str}] {p['title']} [{tags}] — {summary}")

    # Build unread queue
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    queue_lines = []
    for i, p in enumerate(unread, 1):
        tags = ", ".join(p.get("tags", []))
        paper_type = p.get("paper_type", "")
        uploaded = p.get("uploaded_at", "")
        days = 0
        if uploaded:
            try:
                dt = datetime.fromisoformat(uploaded)
                days = (now - dt).days
            except (ValueError, TypeError):
                pass
        type_str = f" ({paper_type})" if paper_type else ""
        citations = p.get("citation_count", 0)
        cite_str = f", {citations} citations" if citations else ""
        queue_lines.append(
            f"{i}. {p['title']} [{tags}]{type_str}{cite_str} — {days} days in queue"
        )

    if not queue_lines:
        return None

    reads_section = "\n".join(reads_lines) if reads_lines else "(no recent reads)"

    prompt = (
        f"I keep a reading queue of research papers. Help me pick the 3 I "
        f"should read next.\n\n"
        f"Papers I've read recently:\n{reads_section}\n\n"
        f"My reading queue:\n" + "\n".join(queue_lines) + "\n\n"
        "Pick exactly 3 papers by number. For each, give one sentence "
        "explaining why I should read it now. Balance:\n"
        "- Relevance to my recent interests\n"
        "- Diversity (don't pick 3 on the same topic)\n"
        "- Queue age (papers sitting too long deserve attention)\n\n"
        "Format:\n[number]. [title] — [reason]\n[number]. [title] — [reason]\n"
        "[number]. [title] — [reason]"
    )

    return _call_claude(prompt, max_tokens=300, model=config.CLAUDE_SMART_MODEL)


def _call_claude(prompt: str, max_tokens: int = 400, model: Optional[str] = None) -> Optional[str]:
    """Call Claude API and return the response text, or None on failure."""
    try:
        import anthropic
    except ImportError:
        log.error(
            "AI summaries require the 'anthropic' package. "
            "Install it with: pip install distillate"
        )
        return None

    try:
        use_model = model or config.CLAUDE_FAST_MODEL
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=use_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        log.info("Generated summary (%d chars, model=%s)", len(text), use_model)
        return text
    except Exception:
        log.exception("Failed to generate summary via Claude API")
        return None


_PENDING_SUMMARY = "(Summary pending — reprocess when API credits are available.)"


def _fallback_read(
    title: str, abstract: str, key_learnings: Optional[List[str]],
) -> Tuple[str, str]:
    """Fallback summaries when Claude API is unavailable."""
    if abstract:
        sentences = abstract.replace("\n", " ").split(". ")
        summary = ". ".join(sentences[:3]).strip()
        if not summary.endswith("."):
            summary += "."
        one_liner = sentences[0].strip()
        if not one_liner.endswith("."):
            one_liner += "."
        return summary, one_liner
    if key_learnings:
        return key_learnings[0], key_learnings[0]
    return _PENDING_SUMMARY, _PENDING_SUMMARY


