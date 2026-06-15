"""Auth router — register, login, logout, me, forgot-password."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.middleware.auth import AuthUser, get_current_user
from app.supabase_client import supabase_admin

router = APIRouter()


# ── Request schemas ────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "teacher"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post("/register", status_code=201)
async def register(body: RegisterRequest):
    """Create a new user account and return an access token."""
    if len(body.password) < 8:
        raise HTTPException(
            422,
            detail={"errors": {"password": ["Password must be at least 8 characters."]}},
        )

    user_role = "teacher"

    try:
        auth_result = supabase_admin.auth.admin.create_user(
            {
                "email": body.email,
                "password": body.password,
                "email_confirm": True,
                "user_metadata": {"full_name": body.name, "role": user_role},
            }
        )
    except Exception as exc:
        msg = str(exc)
        if "already registered" in msg.lower():
            raise HTTPException(
                422,
                detail={"errors": {"email": ["The email has already been taken."]}},
            )
        raise HTTPException(500, detail={"message": msg})

    auth_user = auth_result.user

    # Ensure the profile row has the correct name and role.
    supabase_admin.table("profiles").update(
        {"full_name": body.name, "role": user_role}
    ).eq("id", auth_user.id).execute()

    sign_in = supabase_admin.auth.sign_in_with_password(
        {"email": body.email, "password": body.password}
    )

    return {
        "token": sign_in.session.access_token if sign_in.session else auth_user.id,
        "user": {
            "id": auth_user.id,
            "name": body.name,
            "email": body.email,
            "role": user_role,
            "email_verified_at": str(auth_user.email_confirmed_at),
            "created_at": str(auth_user.created_at),
        },
    }


@router.post("/login")
async def login(body: LoginRequest):
    """Authenticate an existing user and return an access token."""
    try:
        result = supabase_admin.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )
    except Exception:
        raise HTTPException(401, detail={"message": "Invalid credentials."})

    if not result.session:
        raise HTTPException(401, detail={"message": "Invalid credentials."})

    user = result.user
    profile = (
        supabase_admin.table("profiles")
        .select("*")
        .eq("id", user.id)
        .single()
        .execute()
        .data
        or {}
    )

    return {
        "token": result.session.access_token,
        "user": {
            "id": user.id,
            "name": profile.get("full_name", ""),
            "email": user.email,
            "role": profile.get("role", "teacher"),
            "email_verified_at": str(user.email_confirmed_at),
            "created_at": str(user.created_at),
        },
    }


@router.post("/logout")
async def logout(current_user: AuthUser = Depends(get_current_user)):
    """Invalidate the current session token."""
    try:
        supabase_admin.auth.admin.sign_out(current_user.token)
    except Exception:
        pass  # Best-effort; the token will expire on its own.
    return {"message": "Logged out successfully."}


@router.get("/me")
async def me(current_user: AuthUser = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role,
        "email_verified_at": current_user.email_verified_at,
        "created_at": current_user.created_at,
    }


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    """Send a password-reset email (always returns success to prevent user enumeration)."""
    try:
        supabase_admin.auth.reset_password_email(body.email)
    except Exception:
        pass  # Intentionally silent to prevent user enumeration.
    return {"message": "If that email exists, a reset link has been sent."}
