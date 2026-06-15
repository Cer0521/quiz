"""Anthropic Claude AI service — topic-based quiz generation and flashcard generation."""

import json
import os
from typing import List, Optional

import httpx

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"


def _get_api_key() -> str:
    """Return the Anthropic API key or raise a clear error."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set in environment variables. "
            "Add it to your .env file."
        )
    return api_key


def _headers() -> dict:
    return {
        "x-api-key": _get_api_key(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _parse_response(resp: httpx.Response) -> dict:
    """Validate the Anthropic HTTP response and parse the JSON payload."""
    if not resp.is_success:
        body = resp.json() if resp.content else {}
        msg = body.get("error", {}).get("message", "")
        if resp.status_code == 401:
            raise PermissionError("Invalid or missing ANTHROPIC_API_KEY.")
        if resp.status_code == 429:
            raise RuntimeError("Rate limit hit. Please wait a moment and try again.")
        raise RuntimeError(
            f"AI service error ({resp.status_code}): {msg or 'please try again.'}"
        )

    data = resp.json()
    text = data["content"][0]["text"] if data.get("content") else ""

    if not text:
        raise RuntimeError("The AI returned an empty response. Please try again.")

    # Strip markdown fences that the model may add.
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(
            "The AI returned malformed data. Please try again or simplify the request."
        )


# ── Public API ─────────────────────────────────────────────────────────────────


async def generate_quiz_from_topic(
    topic: str,
    total_questions: int,
    sections: list,
    difficulty: str = "medium",
) -> dict:
    """Use Claude to generate quiz questions from a text topic/prompt (no file upload)."""
    sections_desc = "\n".join(
        f"- {s['count']} {s['type']} question(s)" for s in sections
    )

    prompt = f"""Generate exactly {total_questions} quiz questions about the following topic.

TOPIC: {topic}
DIFFICULTY: {difficulty}
SECTIONS REQUIRED:
{sections_desc}

Return ONLY a valid JSON object — no explanation, no markdown, no preamble:
{{
  "questions": [
    {{
      "type": "multiple_choice|true_false|short_answer|essay|enumeration",
      "question_text": "...",
      "correct_answer": "...",
      "options": [{{"text": "...", "is_correct": true}}],
      "points": 1
    }}
  ]
}}

Rules:
- multiple_choice → exactly 4 options, exactly one is_correct: true
- true_false → options: [{{"text": "True", "is_correct": ...}}, {{"text": "False", "is_correct": ...}}]
- short_answer → set correct_answer; options: []
- essay / enumeration → options: []
- Match the requested difficulty: easy = recall, medium = application, hard = analysis/synthesis"""

    payload = {
        "model": MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(ANTHROPIC_API_URL, headers=_headers(), json=payload)

    return _parse_response(resp)


async def generate_flashcards(
    topic: str,
    count: int = 10,
    subject: Optional[str] = None,
) -> dict:
    """Use Claude to generate flashcard study pointers for a topic."""
    context = f" (Subject: {subject})" if subject else ""

    prompt = f"""Generate exactly {count} flashcards for studying: {topic}{context}

Each flashcard is a concise study pointer — a term/concept on one side, a punchy and
memorable explanation on the other. Avoid long paragraphs; aim for clarity and recall.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "flashcards": [
    {{
      "term": "Key term or concept",
      "definition": "Clear, memorable pointer that helps recall this concept",
      "hint": "A short memory hook or mnemonic (optional — use null if none)"
    }}
  ]
}}

Make definitions specific and memorable. Prefer concrete examples over abstract descriptions."""

    payload = {
        "model": MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(ANTHROPIC_API_URL, headers=_headers(), json=payload)

    return _parse_response(resp)
