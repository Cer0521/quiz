"""Authentication dependencies — Bearer token validation via Supabase."""

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.supabase_client import supabase_admin

bearer_scheme = HTTPBearer()


@dataclass
class AuthUser:
    """Authenticated user context attached to every protected request."""

    id: str
    name: str
    email: str
    role: str
    email_verified_at: Optional[str]
    created_at: str
    token: str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> AuthUser:
    """Validate the Bearer token and return the authenticated user."""
    token = credentials.credentials

    try:
        result = supabase_admin.auth.get_user(token)
        user = result.user
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthenticated.")

    if not user:
        raise HTTPException(status_code=401, detail="Unauthenticated.")

    # Fetch the user's profile (may not exist yet for freshly-created accounts).
    try:
        profile_result = (
            supabase_admin.table("profiles")
            .select("*")
            .eq("id", user.id)
            .single()
            .execute()
        )
        profile = profile_result.data or {}
    except Exception:
        profile = {}

    return AuthUser(
        id=user.id,
        name=profile.get("full_name") or (user.user_metadata or {}).get("full_name", ""),
        email=user.email,
        role=profile.get("role", "teacher"),
        email_verified_at=user.email_confirmed_at,
        created_at=str(user.created_at),
        token=token,
    )


def require_verified(
    current_user: AuthUser = Depends(get_current_user),
) -> AuthUser:
    """Dependency that rejects users whose e-mail is not verified (when enforced)."""
    enforce = os.getenv("REQUIRE_EMAIL_VERIFICATION", "false").lower() == "true"
    if enforce and not current_user.email_verified_at:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Your email address is not verified.",
                "code": "EMAIL_NOT_VERIFIED",
            },
        )
    return current_user


def require_teacher(
    current_user: AuthUser = Depends(require_verified),
) -> AuthUser:
    """Dependency that restricts access to the Teacher role."""
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Access denied. Teacher role required.")
    return current_user
