"""Gemini AI service — document-based quiz generation and answer grading."""

import json
import os
import re
from typing import List

import httpx

BASE_URL = (
    "https://generativelanguage.googleapis.com"
    "/v1beta/models/gemini-2.5-flash:generateContent"
)


def _extract_json(text: str) -> str:
    """Strip markdown code fences that Gemini sometimes wraps around JSON."""
    if not text:
        return ""
    trimmed = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", trimmed)
    return match.group(1) if match else trimmed


def _get_api_key() -> str:
    """Return the Gemini API key or raise a clear error."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY is not set in environment variables. "
            "Add it to your .env file."
        )
    return api_key


def _parse_ai_response(resp: httpx.Response) -> dict:
    """Validate the Gemini HTTP response, extract and parse the JSON payload."""
    if not resp.is_success:
        body = resp.json() if resp.content else {}
        msg = body.get("error", {}).get("message", "")
        if resp.status_code == 400:
            raise ValueError(
                f"Request rejected by AI: {msg or 'bad request.'}"
            )
        if resp.status_code == 403:
            raise PermissionError("Invalid or missing GEMINI_API_KEY.")
        if resp.status_code == 429:
            raise RuntimeError(
                "AI rate limit hit. Please wait a moment and try again."
            )
        raise RuntimeError(
            f"AI service error ({resp.status_code}): {msg or 'please try again.'}"
        )

    data = resp.json()
    candidates = data.get("candidates", [])
    ai_text = (
        candidates[0]["content"]["parts"][0]["text"] if candidates else None
    )

    if not ai_text:
        finish_reason = candidates[0].get("finishReason") if candidates else None
        if finish_reason == "SAFETY":
            raise ValueError(
                "The AI flagged the content. Please try different input."
            )
        if finish_reason == "MAX_TOKENS":
            raise ValueError(
                "The AI response was too long. Try reducing the number of questions."
            )
        raise RuntimeError("The AI returned an empty response. Please try again.")

    try:
        return json.loads(_extract_json(ai_text))
    except json.JSONDecodeError:
        raise RuntimeError(
            "The AI returned malformed data. Please try again or simplify the request."
        )


# ── Public API ─────────────────────────────────────────────────────────────────


async def generate_from_document(
    prompt: str, mime_type: str, base64_data: str
) -> dict:
    """Send a document (PDF/text) to Gemini and return structured quiz questions."""
    api_key = _get_api_key()

    payload = {
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
        },
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64_data,
                        }
                    },
                ]
            }
        ],
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{BASE_URL}?key={api_key}", json=payload)

    return _parse_ai_response(resp)


async def grade_essay(
    question: str,
    correct_answer: str,
    student_answer: str,
    max_points: float = 1.0,
) -> dict:
    """Use Gemini to grade a free-text essay answer."""
    api_key = _get_api_key()

    prompt = f"""You are an expert educator grading student essays. Grade the following essay answer.

QUESTION:
{question}

EXPECTED ANSWER/GRADING CRITERIA:
{correct_answer or 'Evaluate based on accuracy, completeness, and clarity.'}

STUDENT'S ANSWER:
{student_answer}

MAXIMUM POINTS: {max_points}

Please evaluate the student's answer and provide:
1. A score from 0 to {max_points} (can be decimal for partial credit)
2. Detailed feedback explaining what was correct, what was incorrect, and suggestions for improvement

Return your response as a JSON object with this exact format:
{{
  "score": <number between 0 and {max_points}>,
  "feedback": "<detailed feedback string>"
}}"""

    payload = {
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
        "contents": [{"parts": [{"text": prompt}]}],
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{BASE_URL}?key={api_key}", json=payload)

    return _parse_ai_response(resp)


async def grade_enumeration(
    question: str,
    correct_answers: List[str],
    student_answers: List[str],
    max_points: float = 1.0,
) -> dict:
    """Use Gemini to grade an enumeration answer set."""
    api_key = _get_api_key()

    prompt = f"""You are an expert educator grading enumeration answers. Grade the following.

QUESTION:
{question}

CORRECT ANSWERS (any of these or close equivalents are acceptable):
{json.dumps(correct_answers)}

STUDENT'S ANSWERS:
{json.dumps(student_answers)}

MAXIMUM POINTS: {max_points}

Award partial credit proportional to correct items identified.
Return JSON:
{{
  "score": <number between 0 and {max_points}>,
  "feedback": "<detailed feedback>",
  "matched": <number of correctly identified items>
}}"""

    payload = {
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
        "contents": [{"parts": [{"text": prompt}]}],
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{BASE_URL}?key={api_key}", json=payload)

    return _parse_ai_response(resp)
