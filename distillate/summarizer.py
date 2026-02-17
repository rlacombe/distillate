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
) -> Tuple[str, str]:
    """Generate summaries for a read paper.

    Returns (summary, one_liner):
      - summary: paragraph summarizing the paper's content and key ideas
      - one_liner: one tight sentence explaining why this paper matters,
        understandable by a non-specialist. Used as blockquote and in reading log.
    """
    if not config.ANTHROPIC_API_KEY:
        return _fallback_read(title, abstract, key_learnings)

    if not abstract:
        return _fallback_read(title, abstract, key_learnings)

    context = f"Abstract: {abstract}"
    if key_learnings:
        context += "\n\nKey takeaways:\n" + "\n".join(f"- {item}" for item in key_learnings)

    prompt = (
        f"You are summarizing a research paper for a personal reading log.\n\n"
        f"Paper: {title}\n\n{context}\n\n"
        f"Provide two things, separated by the exact line '---':\n"
        f"1. A paragraph (3-4 sentences) summarizing the paper. Describe what it "
        f"does, its methods, and findings. State ideas directly as fact — never "
        f"start with 'this paper' or 'the authors'. Include specific methods, "
        f"results, or numbers where possible.\n"
        f"2. ONE sentence (max 20 words) explaining why this work matters — "
        f"what it enables, changes, or makes possible. Focus on the real-world "
        f"impact or implication, not what the paper 'does' or 'claims'. "
        f"Written so a well-educated non-specialist can understand it.\n\n"
        f"Format:\n[paragraph]\n---\n[one sentence]"
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
) -> List[str]:
    """Extract key learnings from a paper's highlights.

    Returns a list of short bullet-point strings, ending with a
    'so what' bullet explaining why this work matters.
    """
    if not config.ANTHROPIC_API_KEY:
        return []

    context_parts = []
    if highlights:
        context_parts.append("Highlights:\n" + "\n".join(f"- {h}" for h in highlights))
    if abstract:
        context_parts.append(f"Abstract: {abstract}")

    if not context_parts:
        return []

    context = "\n\n".join(context_parts)

    prompt = (
        f"From these highlights of \"{title}\":\n\n"
        f"{context}\n\n"
        f"Return 4-6 bullet points:\n"
        f"- First 3-5: key facts or insights. Each one short sentence "
        f"(max 15 words). State facts directly, no filler.\n"
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
        cleaned = re.sub(r"^\d+[.)]\s*", "", line)
        cleaned = re.sub(r"^[-*]\s*", "", cleaned)
        cleaned = re.sub(r"^\*\*.*?\*\*\s*", "", cleaned)
        cleaned = re.sub(r"^(LEARNINGS|SO WHAT):?\s*", "", cleaned, flags=re.IGNORECASE)
        if cleaned:
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
            "Install it with: pip install distillate[ai]"
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
    return f"Read *{title}*.", f"Read *{title}*."


