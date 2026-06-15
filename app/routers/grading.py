"""Grading router — AI-powered essay and enumeration grading via Gemini."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import AuthUser, get_current_user
from app.services import gemini

router = APIRouter()


# ── Request schemas ────────────────────────────────────────────────────────────


class EssayGradeRequest(BaseModel):
    question: str
    correct_answer: Optional[str] = None
    student_answer: str
    max_points: float = 1.0


class EnumerationGradeRequest(BaseModel):
    question: str
    correct_answers: List[str] = []
    student_answers: List[str]
    max_points: float = 1.0


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post("/grade-essay")
async def grade_essay(
    body: EssayGradeRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """Grade a free-text essay answer using Gemini AI."""
    if not body.question or not body.student_answer:
        raise HTTPException(400, detail={"message": "Question and student answer are required."})

    try:
        result = await gemini.grade_essay(
            body.question,
            body.correct_answer or "",
            body.student_answer,
            body.max_points,
        )
        return result
    except Exception as exc:
        raise HTTPException(500, detail={"message": str(exc) or "Grading failed."})


@router.post("/grade-enumeration")
async def grade_enumeration(
    body: EnumerationGradeRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """Grade an enumeration answer set using Gemini AI."""
    if not body.question or not body.student_answers:
        raise HTTPException(400, detail={"message": "Question and student answers are required."})

    try:
        result = await gemini.grade_enumeration(
            body.question,
            body.correct_answers,
            body.student_answers,
            body.max_points,
        )
        return result
    except Exception as exc:
        raise HTTPException(500, detail={"message": str(exc) or "Grading failed."})
