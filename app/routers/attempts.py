"""Attempts router — guest and authenticated quiz-taking, scoring, and history."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import AuthUser, require_verified
from app.supabase_client import supabase_admin

router = APIRouter()


# ── Request schemas ────────────────────────────────────────────────────────────


class AnswersPayload(BaseModel):
    """Map of question-ID → student answer."""

    answers: Dict[str, Any]


class GuestStartPayload(BaseModel):
    """Payload to begin a guest (unauthenticated) quiz attempt."""

    quiz_id: str
    guest_name: Optional[str] = "Guest"
    guest_email: Optional[str] = None


# ── Public: Guest quiz taking ─────────────────────────────────────────────────


@router.post("/quiz/guest/start", status_code=201)
async def start_guest_attempt(body: GuestStartPayload):
    """Start a new guest quiz attempt (no login required)."""
    quiz = (
        supabase_admin.table("quizzes")
        .select("*")
        .eq("id", body.quiz_id)
        .single()
        .execute()
        .data
    )
    if not quiz or not quiz.get("is_published"):
        raise HTTPException(404, detail={"message": "Quiz not found."})

    attempt = (
        supabase_admin.table("quiz_attempts")
        .insert(
            {
                "quiz_id": body.quiz_id,
                "guest_name": body.guest_name,
                "guest_email": body.guest_email,
                "is_guest": True,
                "is_submitted": False,
                "answers": {},
            }
        )
        .execute()
        .data[0]
    )

    questions = (
        supabase_admin.table("questions")
        .select("*, options(*)")
        .eq("quiz_id", body.quiz_id)
        .order("order_index")
        .execute()
        .data
        or []
    )
    for q in questions:
        q.pop("correct_answer", None)
        for opt in q.get("options", []):
            opt.pop("is_correct", None)

    return {"attempt": attempt, "quiz": quiz, "questions": questions}


@router.put("/quiz/guest/attempts/{attempt_id}/answers")
async def save_guest_answers(attempt_id: str, body: AnswersPayload):
    """Auto-save a guest's in-progress answers."""
    attempt = (
        supabase_admin.table("quiz_attempts")
        .select("*")
        .eq("id", attempt_id)
        .eq("is_guest", True)
        .single()
        .execute()
        .data
    )
    if not attempt:
        raise HTTPException(404, detail={"message": "Attempt not found."})

    supabase_admin.table("quiz_attempts").update(
        {"answers": body.answers}
    ).eq("id", attempt_id).execute()

    return {"message": "Answers saved."}


@router.post("/quiz/guest/attempts/{attempt_id}/submit")
async def submit_guest_attempt(attempt_id: str, body: AnswersPayload):
    """Submit a guest attempt for scoring."""
    attempt = (
        supabase_admin.table("quiz_attempts")
        .select("*")
        .eq("id", attempt_id)
        .eq("is_guest", True)
        .single()
        .execute()
        .data
    )
    if not attempt:
        raise HTTPException(404, detail={"message": "Attempt not found."})

    score = _calculate_score(attempt["quiz_id"], body.answers)

    supabase_admin.table("quiz_attempts").update(
        {
            "answers": body.answers,
            "score": score,
            "is_submitted": True,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", attempt_id).execute()

    return {"message": "Submitted.", "score": score}


@router.get("/quiz/guest/attempts/{attempt_id}/result")
async def get_guest_result(attempt_id: str):
    """Retrieve the result of a submitted guest attempt."""
    attempt = (
        supabase_admin.table("quiz_attempts")
        .select("*")
        .eq("id", attempt_id)
        .eq("is_guest", True)
        .single()
        .execute()
        .data
    )
    if not attempt:
        raise HTTPException(404, detail={"message": "Not found."})
    return {"attempt": attempt}


# ── Authenticated quiz taking ─────────────────────────────────────────────────


@router.get("/quizzes/{quiz_id}/attempt")
async def start_or_resume(
    quiz_id: str,
    current_user: AuthUser = Depends(require_verified),
):
    """Start a new attempt or resume an existing in-progress attempt."""
    quiz = (
        supabase_admin.table("quizzes")
        .select("*")
        .eq("id", quiz_id)
        .single()
        .execute()
        .data
    )
    if not quiz or not quiz.get("is_published"):
        raise HTTPException(404, detail={"message": "Quiz not found."})

    # Look for an existing in-progress attempt.
    existing = (
        supabase_admin.table("quiz_attempts")
        .select("*")
        .eq("quiz_id", quiz_id)
        .eq("user_id", current_user.id)
        .eq("is_submitted", False)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )

    if existing:
        attempt = existing[0]
    else:
        attempt = (
            supabase_admin.table("quiz_attempts")
            .insert(
                {
                    "quiz_id": quiz_id,
                    "user_id": current_user.id,
                    "is_guest": False,
                    "is_submitted": False,
                    "answers": {},
                }
            )
            .execute()
            .data[0]
        )

    questions = (
        supabase_admin.table("questions")
        .select("*, options(*)")
        .eq("quiz_id", quiz_id)
        .order("order_index")
        .execute()
        .data
        or []
    )
    for q in questions:
        q.pop("correct_answer", None)
        for opt in q.get("options", []):
            opt.pop("is_correct", None)

    return {"attempt": attempt, "quiz": quiz, "questions": questions}


@router.put("/attempts/{attempt_id}/answers")
async def save_answers(
    attempt_id: str,
    body: AnswersPayload,
    current_user: AuthUser = Depends(require_verified),
):
    """Auto-save an authenticated user's in-progress answers."""
    attempt = (
        supabase_admin.table("quiz_attempts")
        .select("*")
        .eq("id", attempt_id)
        .eq("user_id", current_user.id)
        .single()
        .execute()
        .data
    )
    if not attempt:
        raise HTTPException(404, detail={"message": "Not found."})

    supabase_admin.table("quiz_attempts").update(
        {"answers": body.answers}
    ).eq("id", attempt_id).execute()

    return {"message": "Answers saved."}


@router.post("/attempts/{attempt_id}/submit")
async def submit(
    attempt_id: str,
    body: AnswersPayload,
    current_user: AuthUser = Depends(require_verified),
):
    """Submit an authenticated attempt for scoring."""
    attempt = (
        supabase_admin.table("quiz_attempts")
        .select("*")
        .eq("id", attempt_id)
        .eq("user_id", current_user.id)
        .single()
        .execute()
        .data
    )
    if not attempt:
        raise HTTPException(404, detail={"message": "Not found."})

    score = _calculate_score(attempt["quiz_id"], body.answers)

    supabase_admin.table("quiz_attempts").update(
        {
            "answers": body.answers,
            "score": score,
            "is_submitted": True,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", attempt_id).execute()

    return {"message": "Submitted.", "score": score}


@router.get("/attempts/history")
async def history(
    current_user: AuthUser = Depends(require_verified),
):
    """Return all submitted attempts for the current user."""
    attempts = (
        supabase_admin.table("quiz_attempts")
        .select("*, quizzes(title, subject)")
        .eq("user_id", current_user.id)
        .eq("is_submitted", True)
        .order("submitted_at", desc=True)
        .execute()
        .data
        or []
    )
    return {"attempts": attempts}


@router.get("/attempts/{attempt_id}/result")
async def get_result(
    attempt_id: str,
    current_user: AuthUser = Depends(require_verified),
):
    """Retrieve the result of a submitted authenticated attempt."""
    attempt = (
        supabase_admin.table("quiz_attempts")
        .select("*")
        .eq("id", attempt_id)
        .eq("user_id", current_user.id)
        .single()
        .execute()
        .data
    )
    if not attempt:
        raise HTTPException(404, detail={"message": "Not found."})
    return {"attempt": attempt}


# ── Scoring helper ────────────────────────────────────────────────────────────

# Only these types are auto-graded; essay/enumeration are handled by AI grading.
_AUTO_GRADABLE = {"multiple_choice", "true_false", "short_answer"}


def _calculate_score(quiz_id: str, answers: dict) -> float:
    """Score objective question types; essay/enumeration are left at 0 for AI grading."""
    questions = (
        supabase_admin.table("questions")
        .select("*, options(*)")
        .eq("quiz_id", quiz_id)
        .execute()
        .data
        or []
    )
    # Only count auto-gradable questions toward total_points so that
    # unanswered essay/enumeration items don't deflate the displayed score.
    gradeable = [q for q in questions if q.get("type", "multiple_choice") in _AUTO_GRADABLE]
    total_points = sum(q.get("points", 1) for q in gradeable)
    earned = 0.0

    for q in questions:
        q_id = str(q["id"])
        answer = answers.get(q_id)
        if answer is None:
            continue

        q_type = q.get("type", "multiple_choice")

        if q_type in ("multiple_choice", "true_false"):
            correct_opt = next(
                (o for o in q.get("options", []) if o.get("is_correct")),
                None,
            )
            if correct_opt and str(answer) == str(correct_opt["id"]):
                earned += q.get("points", 1)

        elif q_type == "short_answer":
            if str(answer).strip().lower() == (q.get("correct_answer") or "").strip().lower():
                earned += q.get("points", 1)

        # essay / enumeration: left at 0 until AI grading.

    return round((earned / total_points) * 100, 1) if total_points > 0 else 0.0
