"""
auth_endpoints.py — signup/login/session-check/logout for the landing-
page gate. See services/auth/db.py for the storage/crypto details.
"""

import logging

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

from ..core.rate_limit import limiter
from ..services.auth import db as auth_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=200)


def _auth_response(user: dict) -> dict:
    token = auth_db.create_session(user["id"])
    return {"token": token, "email": user["email"]}


@router.post("/signup", summary="Create an account")
@limiter.limit("5/minute")
async def signup(req: SignupRequest, request: Request):
    if not auth_db.is_valid_email(req.email):
        raise HTTPException(status_code=422, detail="That doesn't look like a valid email address.")
    user = auth_db.create_user(req.email, req.password)
    if not user:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    return _auth_response(user)


@router.post("/login", summary="Log in")
@limiter.limit("10/minute")
async def login(req: LoginRequest, request: Request):
    user = auth_db.authenticate(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    return _auth_response(user)


@router.get("/me", summary="Check the current session")
async def me(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in.")
    user = auth_db.get_user_for_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    return {"email": user["email"]}


@router.post("/logout", summary="Log out")
async def logout(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    if token:
        auth_db.delete_session(token)
    return {"logged_out": True}
