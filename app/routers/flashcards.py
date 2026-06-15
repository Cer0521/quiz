"""Flashcards router — AI-generated and manual flashcard sets for study."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.middleware.auth import AuthUser, require_verified
from app.services import anthropic_ai
from app.supabase_client import supabase_admin

router = APIRouter()


# ── Request schemas ────────────────────────────────────────────────────────────


class GenerateFlashcardsRequest(BaseModel):
    """Payload for AI-generating a flashcard set."""

    topic: str
    count: int = Field(default=10, ge=1, le=50)
    subject: Optional[str] = None
    title: Optional[str] = None


class FlashcardItem(BaseModel):
    """A single flashcard term/definition pair."""

    term: str
    definition: str
    hint: Optional[str] = None
    order_index: Optional[int] = None


class CreateFlashcardSetRequest(BaseModel):
    """Payload for creating a flashcard set manually."""

    title: str
    subject: Optional[str] = None
    description: Optional[str] = None
    flashcards: List[FlashcardItem] = []


# ── AI generation ─────────────────────────────────────────────────────────────


@router.post("/flashcard-sets/generate", status_code=201)
async def generate_flashcard_set(
    body: GenerateFlashcardsRequest,
    current_user: AuthUser = Depends(require_verified),
):
    """Generate a flashcard set from a topic using Claude AI.

    Returns a list of study-pointer flashcards (term → definition + optional hint).
    """
    try:
        ai_result = await anthropic_ai.generate_flashcards(
            topic=body.topic,
            count=body.count,
            subject=body.subject,
        )
    except Exception as exc:
        raise HTTPException(500, detail={"message": str(exc)})

    title = body.title or f"Flashcards: {body.topic}"
    set_insert = (
        supabase_admin.table("flashcard_sets")
        .insert(
            {
                "title": title,
                "subject": body.subject,
                "description": f"AI-generated study pointers for: {body.topic}",
                "user_id": current_user.id,
            }
        )
        .execute()
    )
    flashcard_set = set_insert.data[0]

    cards = ai_result.get("flashcards", [])
    saved_cards = []
    for idx, card in enumerate(cards):
        ins = (
            supabase_admin.table("flashcards")
            .insert(
                {
                    "set_id": flashcard_set["id"],
                    "term": card.get("term", ""),
                    "definition": card.get("definition", ""),
                    "hint": card.get("hint"),
                    "order_index": idx,
                }
            )
            .execute()
        )
        saved_cards.append(ins.data[0])

    flashcard_set["flashcards"] = saved_cards
    return {
        "flashcard_set": flashcard_set,
        "message": f"Generated {len(saved_cards)} flashcards successfully.",
    }


# ── Manual creation ────────────────────────────────────────────────────────────


@router.post("/flashcard-sets", status_code=201)
async def create_flashcard_set(
    body: CreateFlashcardSetRequest,
    current_user: AuthUser = Depends(require_verified),
):
    """Create a flashcard set manually."""
    set_insert = (
        supabase_admin.table("flashcard_sets")
        .insert(
            {
                "title": body.title,
                "subject": body.subject,
                "description": body.description,
                "user_id": current_user.id,
            }
        )
        .execute()
    )
    flashcard_set = set_insert.data[0]

    saved_cards = []
    for idx, card in enumerate(body.flashcards):
        ins = (
            supabase_admin.table("flashcards")
            .insert(
                {
                    "set_id": flashcard_set["id"],
                    "term": card.term,
                    "definition": card.definition,
                    "hint": card.hint,
                    "order_index": card.order_index if card.order_index is not None else idx,
                }
            )
            .execute()
        )
        saved_cards.append(ins.data[0])

    flashcard_set["flashcards"] = saved_cards
    return {"flashcard_set": flashcard_set}


# ── List ───────────────────────────────────────────────────────────────────────


@router.get("/flashcard-sets")
async def list_flashcard_sets(
    current_user: AuthUser = Depends(require_verified),
):
    """List all flashcard sets owned by the current user."""
    sets = (
        supabase_admin.table("flashcard_sets")
        .select("*")
        .eq("user_id", current_user.id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )

    for s in sets:
        count_result = (
            supabase_admin.table("flashcards")
            .select("id", count="exact", head=True)
            .eq("set_id", s["id"])
            .execute()
        )
        s["card_count"] = count_result.count or 0

    return {"flashcard_sets": sets}


# ── Get single set ────────────────────────────────────────────────────────────


@router.get("/flashcard-sets/{set_id}")
async def get_flashcard_set(
    set_id: str,
    current_user: AuthUser = Depends(require_verified),
):
    """Fetch a single flashcard set with all its cards."""
    flashcard_set = (
        supabase_admin.table("flashcard_sets")
        .select("*")
        .eq("id", set_id)
        .single()
        .execute()
        .data
    )
    if not flashcard_set:
        raise HTTPException(404, detail={"message": "Flashcard set not found."})
    if flashcard_set["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    cards = (
        supabase_admin.table("flashcards")
        .select("*")
        .eq("set_id", set_id)
        .order("order_index")
        .execute()
        .data
        or []
    )
    flashcard_set["flashcards"] = cards
    return {"flashcard_set": flashcard_set}


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/flashcard-sets/{set_id}", status_code=204)
async def delete_flashcard_set(
    set_id: str,
    current_user: AuthUser = Depends(require_verified),
):
    """Delete a flashcard set and all its cards."""
    flashcard_set = (
        supabase_admin.table("flashcard_sets")
        .select("user_id")
        .eq("id", set_id)
        .single()
        .execute()
        .data
    )
    if not flashcard_set or flashcard_set["user_id"] != current_user.id:
        raise HTTPException(403, detail={"message": "Forbidden."})

    supabase_admin.table("flashcard_sets").delete().eq("id", set_id).execute()
    return None
