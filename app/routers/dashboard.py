"""Dashboard router — aggregated statistics for the teacher dashboard."""

from fastapi import APIRouter, Depends

from app.middleware.auth import AuthUser, require_teacher
from app.supabase_client import supabase_admin

router = APIRouter()


@router.get("/stats")
async def stats(current_user: AuthUser = Depends(require_teacher)):
    """Return high-level statistics for the authenticated teacher."""

    # Total quizzes owned by this teacher.
    quiz_count = (
        supabase_admin.table("quizzes")
        .select("id", count="exact", head=True)
        .eq("user_id", current_user.id)
        .execute()
    )
    total_quizzes = quiz_count.count or 0

    # Collect quiz IDs to query their attempts.
    quiz_ids_result = (
        supabase_admin.table("quizzes")
        .select("id")
        .eq("user_id", current_user.id)
        .execute()
    )
    quiz_ids = [q["id"] for q in (quiz_ids_result.data or [])]

    total_attempts = 0
    avg_score = 0.0

    if quiz_ids:
        attempts = (
            supabase_admin.table("quiz_attempts")
            .select("score")
            .in_("quiz_id", quiz_ids)
            .eq("is_submitted", True)
            .execute()
            .data
            or []
        )
        total_attempts = len(attempts)
        if attempts:
            scores = [a.get("score") or 0 for a in attempts]
            avg_score = round(sum(scores) / len(scores), 1)

    # Subscription info (fallback to free-tier defaults if missing).
    try:
        sub = (
            supabase_admin.table("subscriptions")
            .select("*")
            .eq("user_id", current_user.id)
            .single()
            .execute()
            .data
        ) or {"tier": "free", "quiz_count_this_period": 0}
    except Exception:
        sub = {"tier": "free", "quiz_count_this_period": 0}

    return {
        "total_quizzes": total_quizzes,
        "total_attempts": total_attempts,
        "average_score": avg_score,
        "subscription": sub,
    }
