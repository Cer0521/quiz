"""Subscription router — tier management, referrals, and usage limits."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import AuthUser, get_current_user
from app.supabase_client import supabase_admin

router = APIRouter()


# ── Request schemas ────────────────────────────────────────────────────────────


class UpgradeRequest(BaseModel):
    plan: str = "premium"
    payment_token: str = ""


class ReferralRequest(BaseModel):
    code: str


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("")
async def get_subscription(
    current_user: AuthUser = Depends(get_current_user),
):
    """Return the current user's subscription details."""
    sub = (
        supabase_admin.table("subscriptions")
        .select("*")
        .eq("user_id", current_user.id)
        .single()
        .execute()
        .data
    )
    if not sub:
        raise HTTPException(404, detail={"message": "Subscription not found."})
    return {"subscription": sub}


@router.post("/upgrade")
async def upgrade_subscription(
    body: UpgradeRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """Upgrade the user to the Premium tier."""
    now = datetime.now(timezone.utc)
    updated = (
        supabase_admin.table("subscriptions")
        .update(
            {
                "tier": "premium",
                "period_start": now.isoformat(),
                "period_end": (now + timedelta(days=30)).isoformat(),
            }
        )
        .eq("user_id", current_user.id)
        .execute()
    )
    return {
        "subscription": updated.data[0] if updated.data else {},
        "message": "Upgraded to Premium.",
    }


@router.post("/referral")
async def apply_referral(
    body: ReferralRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """Validate and apply a referral code."""
    referrer = (
        supabase_admin.table("profiles")
        .select("id")
        .eq("referral_code", body.code)
        .single()
        .execute()
        .data
    )
    if not referrer:
        raise HTTPException(404, detail={"message": "Invalid referral code."})
    return {"message": "Referral applied successfully."}


@router.get("/referral-stats")
async def referral_stats(
    current_user: AuthUser = Depends(get_current_user),
):
    """Return how many users were referred by the current user."""
    referrals = (
        supabase_admin.table("profiles")
        .select("id", count="exact", head=True)
        .eq("referred_by", current_user.id)
        .execute()
    )
    return {"referral_count": referrals.count or 0}


@router.get("/limits")
async def check_limits(
    current_user: AuthUser = Depends(get_current_user),
):
    """Return the user's current usage vs. their tier limits."""
    sub = (
        supabase_admin.table("subscriptions")
        .select("*")
        .eq("user_id", current_user.id)
        .single()
        .execute()
        .data
        or {}
    )
    tier = sub.get("tier", "free")
    used = sub.get("quiz_count_this_period", 0)
    limit = 5 if tier == "free" else None

    return {
        "tier": tier,
        "used": used,
        "limit": limit,
        "can_create": limit is None or used < limit,
    }
