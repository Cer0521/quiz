"""Quizzes router — CRUD, AI generation, question management, and analytics."""

import base64
import json
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.middleware.auth import (
    AuthUser,
    get_current_user,
    require_teacher,
    require_verified,
)
from app.services import gemini, anthropic_ai
from app.supabase_client import supabase_admin

router = APIRouter()

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


# ── Pydantic request schemas ──────────────────────────────────────────────────


class QuizSection(BaseModel):
    """A single section in an AI-generated quiz request."""

    type: str
    count: int


class ManualQuizRequest(BaseModel):
    """Payload for creating a quiz manually (without AI generation)."""

    title: str
    description: Optional[str] = None
    subject: Optional[str] = None
    difficulty: Optional[str] = "medium"
    time_limit: Optional[int] = None
    questions: Optional[List[dict]] = []


class QuizUpdateRequest(BaseModel):
    """Partial-update payload for an existing quiz."""

    title: Optional[str] = None
    description: Optional[str] = None
    subject: Optional[str] = None
    difficulty: Optional[str] = None
    time_limit: Optional[int] = None
    is_published: Optional[bool] = None


class QuestionRequest(BaseModel):
    """Payload for adding or updating a single question."""

    type: str
    question_text: str
    points: float = 1.0
    order_index: Optional[int] = None
    correct_answer: Optional[str] = None
    options: Optional[List[dict]] = None
    correct_answers: Optional[List[str]] = None


# ── Public: quiz by share code ────────────────────────────────────────────────


@router.get("/quiz/share/{code}")
async def get_by_share_code(code: str):
    """Fetch a published quiz by its public share code (answers stripped)."""
    result = (
        supabase_admin.table("quizzes")
        .select("*")
        .eq("share_code", code)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(404, detail={"message": "Quiz not found."})

    quiz = result.data
    if not quiz.get("is_published"):
        raise HTTPException(404, detail={"message": "Quiz not found."})

    questions = (
        supabase_admin.table("questions")
        .select("*, options(*)")
        .eq("quiz_id", quiz["id"])
        .order("order_index")
        .execute()
        .data
        or []
    )

    # Strip correct answers so quiz-takers can't peek.
    for q in questions:
        q.pop("correct_answer", None)
        q.pop("correct_answers", None)
        for opt in q.get("options", []):
            opt.pop("is_correct", None)

    quiz["questions"] = questions
    return {"quiz": quiz}


# ── Teacher: list own quizzes ─────────────────────────────────────────────────


@router.get("/quizzes")
async def index(current_user: AuthUser = Depends(require_verified)):
    """List all quizzes owned by the authenticated teacher."""
    quizzes = (
        supabase_admin.table("quizzes")
        .select(
            "id, title, description, subject, difficulty, "
            "is_published, time_limit, share_code, created_at"
        )
        .eq("user_id", current_user.id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )

    for quiz in quizzes:
        q_count = (
            supabase_admin.table("questions")
            .select("id", count="exact", head=True)
            .eq("quiz_id", quiz["id"])
            .execute()
        )
        a_count = (
            supabase_admin.table("quiz_attempts")
            .select("id", count="exact", head=True)
            .eq("quiz_id", quiz["id"])
            .execute()
        )
        quiz["total_questions"] = q_count.count or 0
        quiz["attempt_count"] = a_count.count or 0

    return {"quizzes": quizzes}


# ── Teacher: get single quiz ──────────────────────────────────────────────────


@router.get("/quizzes/{quiz_id}")
async def show(quiz_id: str, current_user: AuthUser = Depends(require_verified)):
    """Fetch a single quiz (with questions) by ID."""
    result = (
        supabase_admin.table("quizzes")
        .select("*")
        .eq("id", quiz_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(404, detail={"message": "Not found."})

    quiz = result.data
    if quiz["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    questions = (
        supabase_admin.table("questions")
        .select("*, options(*)")
        .eq("quiz_id", quiz_id)
        .order("order_index")
        .execute()
        .data
        or []
    )
    quiz["questions"] = questions
    return {"quiz": quiz}


# ── Teacher: create quiz via AI upload ────────────────────────────────────────


@router.post("/quizzes/generate", status_code=201)
async def store(
    title: str = Form(...),
    total_questions: int = Form(...),
    sections: str = Form(...),
    time_limit: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    document: UploadFile = File(...),
    current_user: AuthUser = Depends(require_teacher),
):
    """Upload a document and let Gemini AI generate quiz questions from it."""
    if document.content_type not in ("application/pdf", "text/plain"):
        raise HTTPException(
            422,
            detail={"errors": {"document": ["Document must be a PDF or text file."]}},
        )

    file_bytes = await document.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            422,
            detail={"errors": {"document": ["Document must be smaller than 5 MB."]}},
        )

    try:
        parsed_sections = json.loads(sections)
    except json.JSONDecodeError:
        raise HTTPException(
            422, detail={"errors": {"sections": ["Invalid sections format."]}}
        )

    total_assigned = sum(int(s["count"]) for s in parsed_sections)
    if total_assigned != total_questions:
        raise HTTPException(
            422,
            detail={
                "errors": {
                    "sections": [
                        f"Sections total ({total_assigned}) must equal "
                        f"Total Questions ({total_questions})."
                    ]
                }
            },
        )

    # ── Subscription limit check ───────────────────────────────────────────
    sub = (
        supabase_admin.table("subscriptions")
        .select("*")
        .eq("user_id", current_user.id)
        .single()
        .execute()
        .data
    )
    if sub and sub.get("tier") == "free":
        if (sub.get("quiz_count_this_period") or 0) >= 5:
            raise HTTPException(
                403,
                detail={
                    "message": "Free tier limit reached. Upgrade to Premium for unlimited quizzes.",
                    "code": "SUBSCRIPTION_LIMIT",
                },
            )

    # ── Call Gemini AI ─────────────────────────────────────────────────────
    mime_type = document.content_type
    base64_data = base64.b64encode(file_bytes).decode("utf-8")

    sections_desc = "\n".join(
        f"- {s['count']} {s['type']} question(s)" for s in parsed_sections
    )
    prompt = (
        f"Generate exactly {total_questions} quiz questions from this document.\n"
        f"Sections required:\n{sections_desc}\n\n"
        "Return a JSON object:\n"
        '{\n'
        '  "questions": [\n'
        '    {\n'
        '      "type": "multiple_choice|true_false|short_answer|essay|enumeration",\n'
        '      "question_text": "...",\n'
        '      "correct_answer": "...",\n'
        '      "options": [{"text": "...", "is_correct": true/false}],\n'
        '      "points": 1\n'
        '    }\n'
        '  ]\n'
        '}\n'
        "Only return valid JSON."
    )

    try:
        ai_result = await gemini.generate_from_document(prompt, mime_type, base64_data)
    except Exception as exc:
        raise HTTPException(500, detail={"message": str(exc)})

    # ── Persist quiz + questions ───────────────────────────────────────────
    share_code = str(uuid.uuid4())[:8].upper()

    quiz_insert = (
        supabase_admin.table("quizzes")
        .insert(
            {
                "title": title,
                "description": description,
                "user_id": current_user.id,
                "time_limit": time_limit,
                "share_code": share_code,
                "is_published": False,
            }
        )
        .execute()
    )
    quiz = quiz_insert.data[0]

    for idx, q in enumerate(ai_result.get("questions", [])):
        q_insert = (
            supabase_admin.table("questions")
            .insert(
                {
                    "quiz_id": quiz["id"],
                    "type": q.get("type", "multiple_choice"),
                    "question_text": q.get("question_text", ""),
                    "correct_answer": q.get("correct_answer"),
                    "points": q.get("points", 1),
                    "order_index": idx,
                }
            )
            .execute()
        )
        question = q_insert.data[0]

        for opt in q.get("options", []):
            supabase_admin.table("options").insert(
                {
                    "question_id": question["id"],
                    "text": opt.get("text", ""),
                    "is_correct": opt.get("is_correct", False),
                }
            ).execute()

    # Increment the free-tier quiz counter.
    if sub and sub.get("tier") == "free":
        supabase_admin.table("subscriptions").update(
            {"quiz_count_this_period": (sub.get("quiz_count_this_period") or 0) + 1}
        ).eq("user_id", current_user.id).execute()

    return {"quiz": quiz, "message": "Quiz generated successfully."}


# ── Teacher: create quiz via topic prompt (Claude AI) ────────────────────────


class QuizFromTopicRequest(BaseModel):
    """Payload for generating a quiz from a plain-text topic using Claude AI."""

    title: str
    topic: str
    total_questions: int
    sections: List[QuizSection]
    difficulty: Optional[str] = "medium"
    time_limit: Optional[int] = None
    description: Optional[str] = None


@router.post("/quizzes/generate-topic", status_code=201)
async def store_from_topic(
    body: QuizFromTopicRequest,
    current_user: AuthUser = Depends(require_teacher),
):
    """Generate a quiz from a text topic/prompt using Claude AI (no file upload needed)."""
    total_assigned = sum(s.count for s in body.sections)
    if total_assigned != body.total_questions:
        raise HTTPException(
            422,
            detail={
                "errors": {
                    "sections": [
                        f"Sections total ({total_assigned}) must equal "
                        f"total_questions ({body.total_questions})."
                    ]
                }
            },
        )

    # ── Subscription limit check ───────────────────────────────────────────
    sub = (
        supabase_admin.table("subscriptions")
        .select("*")
        .eq("user_id", current_user.id)
        .single()
        .execute()
        .data
    )
    if sub and sub.get("tier") == "free":
        if (sub.get("quiz_count_this_period") or 0) >= 5:
            raise HTTPException(
                403,
                detail={
                    "message": "Free tier limit reached. Upgrade to Premium for unlimited quizzes.",
                    "code": "SUBSCRIPTION_LIMIT",
                },
            )

    # ── Call Claude AI ─────────────────────────────────────────────────────
    try:
        ai_result = await anthropic_ai.generate_quiz_from_topic(
            topic=body.topic,
            total_questions=body.total_questions,
            sections=[s.model_dump() for s in body.sections],
            difficulty=body.difficulty or "medium",
        )
    except Exception as exc:
        raise HTTPException(500, detail={"message": str(exc)})

    # ── Persist quiz + questions ───────────────────────────────────────────
    share_code = str(uuid.uuid4())[:8].upper()

    quiz_insert = (
        supabase_admin.table("quizzes")
        .insert(
            {
                "title": body.title,
                "description": body.description,
                "difficulty": body.difficulty,
                "user_id": current_user.id,
                "time_limit": body.time_limit,
                "share_code": share_code,
                "is_published": False,
            }
        )
        .execute()
    )
    quiz = quiz_insert.data[0]

    for idx, q in enumerate(ai_result.get("questions", [])):
        q_insert = (
            supabase_admin.table("questions")
            .insert(
                {
                    "quiz_id": quiz["id"],
                    "type": q.get("type", "multiple_choice"),
                    "question_text": q.get("question_text", ""),
                    "correct_answer": q.get("correct_answer"),
                    "points": q.get("points", 1),
                    "order_index": idx,
                }
            )
            .execute()
        )
        question = q_insert.data[0]

        for opt in q.get("options", []):
            supabase_admin.table("options").insert(
                {
                    "question_id": question["id"],
                    "text": opt.get("text", ""),
                    "is_correct": opt.get("is_correct", False),
                }
            ).execute()

    if sub and sub.get("tier") == "free":
        supabase_admin.table("subscriptions").update(
            {"quiz_count_this_period": (sub.get("quiz_count_this_period") or 0) + 1}
        ).eq("user_id", current_user.id).execute()

    return {"quiz": quiz, "message": "Quiz generated successfully."}


# ── Teacher: create quiz manually ─────────────────────────────────────────────


@router.post("/quizzes/manual", status_code=201)
async def store_manual(
    body: ManualQuizRequest,
    current_user: AuthUser = Depends(require_teacher),
):
    """Create a quiz by hand (no AI generation)."""
    share_code = str(uuid.uuid4())[:8].upper()

    quiz_insert = (
        supabase_admin.table("quizzes")
        .insert(
            {
                "title": body.title,
                "description": body.description,
                "subject": body.subject,
                "difficulty": body.difficulty,
                "time_limit": body.time_limit,
                "user_id": current_user.id,
                "share_code": share_code,
                "is_published": False,
            }
        )
        .execute()
    )
    quiz = quiz_insert.data[0]

    for idx, q in enumerate(body.questions or []):
        q_ins = (
            supabase_admin.table("questions")
            .insert(
                {
                    "quiz_id": quiz["id"],
                    "type": q.get("type", "multiple_choice"),
                    "question_text": q.get("question_text", ""),
                    "correct_answer": q.get("correct_answer"),
                    "points": q.get("points", 1),
                    "order_index": q.get("order_index", idx),
                }
            )
            .execute()
        )
        question = q_ins.data[0]

        for opt in q.get("options", []):
            supabase_admin.table("options").insert(
                {
                    "question_id": question["id"],
                    "text": opt.get("text", ""),
                    "is_correct": opt.get("is_correct", False),
                }
            ).execute()

    return {"quiz": quiz}


# ── Teacher: update quiz ──────────────────────────────────────────────────────


@router.patch("/quizzes/{quiz_id}")
async def update(
    quiz_id: str,
    body: QuizUpdateRequest,
    current_user: AuthUser = Depends(require_teacher),
):
    """Partially update a quiz's metadata."""
    existing = (
        supabase_admin.table("quizzes")
        .select("user_id")
        .eq("id", quiz_id)
        .single()
        .execute()
        .data
    )
    if not existing:
        raise HTTPException(404, detail={"message": "Not found."})
    if existing["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = (
        supabase_admin.table("quizzes")
        .update(updates)
        .eq("id", quiz_id)
        .execute()
    )
    return {"quiz": updated.data[0] if updated.data else {}}


# ── Teacher: publish quiz ─────────────────────────────────────────────────────


@router.post("/quizzes/{quiz_id}/publish")
async def publish(
    quiz_id: str,
    current_user: AuthUser = Depends(require_teacher),
):
    """Mark a quiz as published so students can access it via share code."""
    existing = (
        supabase_admin.table("quizzes")
        .select("user_id")
        .eq("id", quiz_id)
        .single()
        .execute()
        .data
    )
    if not existing or existing["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    updated = (
        supabase_admin.table("quizzes")
        .update({"is_published": True})
        .eq("id", quiz_id)
        .execute()
    )
    return {"quiz": updated.data[0] if updated.data else {}}


# ── Teacher: delete quiz ──────────────────────────────────────────────────────


@router.delete("/quizzes/{quiz_id}", status_code=204)
async def destroy(
    quiz_id: str,
    current_user: AuthUser = Depends(require_teacher),
):
    """Permanently delete a quiz and its associated data."""
    existing = (
        supabase_admin.table("quizzes")
        .select("user_id")
        .eq("id", quiz_id)
        .single()
        .execute()
        .data
    )
    if not existing or existing["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    supabase_admin.table("quizzes").delete().eq("id", quiz_id).execute()
    return None


# ── Teacher: question management ──────────────────────────────────────────────


@router.post("/quizzes/{quiz_id}/questions", status_code=201)
async def add_question(
    quiz_id: str,
    body: QuestionRequest,
    current_user: AuthUser = Depends(require_teacher),
):
    """Add a new question to a quiz."""
    quiz = (
        supabase_admin.table("quizzes")
        .select("user_id")
        .eq("id", quiz_id)
        .single()
        .execute()
        .data
    )
    if not quiz or quiz["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    q_ins = (
        supabase_admin.table("questions")
        .insert(
            {
                "quiz_id": quiz_id,
                "type": body.type,
                "question_text": body.question_text,
                "correct_answer": body.correct_answer,
                "correct_answers": body.correct_answers,
                "points": body.points,
                "order_index": body.order_index or 0,
            }
        )
        .execute()
    )
    question = q_ins.data[0]

    for opt in body.options or []:
        supabase_admin.table("options").insert(
            {
                "question_id": question["id"],
                "text": opt.get("text", ""),
                "is_correct": opt.get("is_correct", False),
            }
        ).execute()

    return {"question": question}


@router.put("/quizzes/{quiz_id}/questions/{question_id}")
async def update_question(
    quiz_id: str,
    question_id: str,
    body: QuestionRequest,
    current_user: AuthUser = Depends(require_teacher),
):
    """Update an existing question."""
    quiz = (
        supabase_admin.table("quizzes")
        .select("user_id")
        .eq("id", quiz_id)
        .single()
        .execute()
        .data
    )
    if not quiz or quiz["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    updates = {
        k: v
        for k, v in body.model_dump(exclude={"options", "correct_answers"}).items()
        if v is not None
    }
    # correct_answers may be an empty list (valid), so check explicitly for None.
    if body.correct_answers is not None:
        updates["correct_answers"] = body.correct_answers

    updated = (
        supabase_admin.table("questions")
        .update(updates)
        .eq("id", question_id)
        .execute()
    )

    # Replace options when the caller provides them.
    if body.options is not None:
        supabase_admin.table("options").delete().eq("question_id", question_id).execute()
        for opt in body.options:
            supabase_admin.table("options").insert(
                {
                    "question_id": question_id,
                    "text": opt.get("text", ""),
                    "is_correct": opt.get("is_correct", False),
                }
            ).execute()

    return {"question": updated.data[0] if updated.data else {}}


@router.delete("/quizzes/{quiz_id}/questions/{question_id}", status_code=204)
async def delete_question(
    quiz_id: str,
    question_id: str,
    current_user: AuthUser = Depends(require_teacher),
):
    """Remove a question from a quiz."""
    quiz = (
        supabase_admin.table("quizzes")
        .select("user_id")
        .eq("id", quiz_id)
        .single()
        .execute()
        .data
    )
    if not quiz or quiz["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    supabase_admin.table("questions").delete().eq("id", question_id).execute()
    return None


# ── Teacher: analytics ────────────────────────────────────────────────────────


@router.get("/quizzes/{quiz_id}/analytics")
async def analytics(
    quiz_id: str,
    current_user: AuthUser = Depends(require_teacher),
):
    """Return attempt statistics for a specific quiz."""
    quiz = (
        supabase_admin.table("quizzes")
        .select("user_id")
        .eq("id", quiz_id)
        .single()
        .execute()
        .data
    )
    if not quiz or quiz["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    attempts = (
        supabase_admin.table("quiz_attempts")
        .select("score, created_at")
        .eq("quiz_id", quiz_id)
        .eq("is_submitted", True)
        .execute()
        .data
        or []
    )
    scores = [a["score"] or 0 for a in attempts]
    avg = sum(scores) / len(scores) if scores else 0

    return {
        "total_attempts": len(scores),
        "average_score": round(avg, 1),
        "scores": scores,
    }
