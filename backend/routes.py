"""
routes.py
----------
All API routes for the Accounting Command Center, organized by domain:

  /api/auth/*        - login / current session
  /api/admin/users/*  - user management (Super Admin + Admin)
  /api/admin/audit    - master audit log (Super Admin + Admin)
  /api/assignments/*  - employee <-> client assignment (Super Admin + Admin)
  /api/dashboard/*    - scoped operational data (Employee, masked)
  /api/data/*         - scoped "data cleaner" rows (Employee, masked)
  /api/client/*       - client-only self-service endpoints
"""

import json
import os
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import google.generativeai as genai
except ImportError:
    genai = None
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field

from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_min_rank,
    require_role,
    verify_password,
)
from database import get_db, log_audit

router = APIRouter()

# --------------------------------------------------------------------------
# Uploaded document storage
# --------------------------------------------------------------------------
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Node.js automation layer -- the FastAPI backend is the trusted gateway that
# stamps actor_id/actor_role onto every Composio dispatch after verifying the
# caller's JWT itself, per the security note in README.md. The browser never
# talks to automation.js directly.
# --------------------------------------------------------------------------
AUTOMATION_BASE_URL = os.environ.get("AUTOMATION_BASE_URL", "http://localhost:4000")

# --------------------------------------------------------------------------
# Gemini-powered client report narration -- the first "real AI" tier: no
# Composio connector or bank feed needed, it just narrates data we already
# have (invoices, checklist, messages, chaser campaigns) in plain language.
#
# The API key is a runtime setting a Super Admin configures from Global
# Settings (stored in app_settings), not an environment variable -- so it
# can be set/rotated without restarting the server. GEMINI_API_KEY/
# GEMINI_MODEL env vars are still honored as a fallback default.
# --------------------------------------------------------------------------
GEMINI_MODEL_DEFAULT = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def _get_app_setting(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] else None


def _set_app_setting(conn, key: str, value: str, updated_by: int) -> None:
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
        (key, value, updated_by),
    )


def _get_gemini_config(conn) -> tuple[Optional[str], str]:
    api_key = _get_app_setting(conn, "gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    model = _get_app_setting(conn, "gemini_model") or GEMINI_MODEL_DEFAULT
    return (api_key or None), model


def _generate_client_report_text(
    client_name: str, invoices: list, checklist_items: list, messages: list, campaigns: list,
    api_key: Optional[str], model_name: str,
) -> str:
    if genai is None:
        raise HTTPException(
            status_code=503,
            detail="The google-generativeai package is not installed on the server. Run "
                   "'pip install -r requirements.txt' in backend/ and restart.",
        )
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Gemini API key is not configured. A Super Admin can set it under Global Settings.",
        )

    prompt = (
        "You are an accounting assistant preparing a short internal status report for an "
        "account officer about one of their clients. Using only the data below, write a "
        "concise, plain-language summary (150-250 words, plain prose paragraphs, no markdown "
        "headers or bullet lists) covering: overall invoice/payment standing, document "
        "checklist completeness, any collections concerns, and one recommended next action.\n\n"
        f"Client: {client_name}\n"
        f"Invoices: {json.dumps(invoices)}\n"
        f"Checklist items: {json.dumps(checklist_items)}\n"
        f"Recent messages: {json.dumps(messages)}\n"
        f"Chaser campaigns: {json.dumps(campaigns)}\n"
    )

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        return response.text
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini request failed: {exc}")

# --------------------------------------------------------------------------
# Simulated AI-agent suggestion bank. Nothing here calls a real model -- this
# is a stand-in for the output an AI agent (reading a bank feed, an inbox, or
# ledger data via Composio-connected tools) would produce. The real part is
# what happens next: every suggestion sits in a pending queue and requires an
# explicit human Approve before anything is written to the ledger or sent to
# a client.
# --------------------------------------------------------------------------
AI_SUGGESTION_TEMPLATES = [
    {
        "suggestion_type": "reconciliation_flag",
        "title": "Unmatched bank transaction",
        "detail": "Found a debit with no matching ledger entry on this client's feed. Flagging for manual review.",
        "build_payload": lambda: {"row_label": f"Unmatched debit ${random.choice([182, 305, 482, 910])}.{random.randint(10,99)}", "flag": "needs_review"},
    },
    {
        "suggestion_type": "reconciliation_flag",
        "title": "Duplicate transaction suspected",
        "detail": "Two entries of the same amount posted a day apart look like a duplicate charge. Flagging for review.",
        "build_payload": lambda: {"row_label": f"Possible duplicate ${random.choice([600, 1200, 2400])}.00", "flag": "needs_review"},
    },
    {
        "suggestion_type": "client_reminder",
        "title": "Draft reminder: outstanding documents",
        "detail": "This client has outstanding checklist items. Drafted a friendly reminder for your review.",
        "build_payload": lambda: {"body": "Hi! Just a friendly reminder we're still waiting on a couple of documents from you — upload whenever you get a chance. Thanks!"},
    },
    {
        "suggestion_type": "client_reminder",
        "title": "Draft reminder: filing deadline approaching",
        "detail": "Detected an upcoming filing deadline for this client. Drafted a heads-up message for your review.",
        "build_payload": lambda: {"body": "Just a heads up — your filing deadline is coming up soon. Let us know if you need anything from us to get ready."},
    },
    {
        "suggestion_type": "invoice_draft",
        "title": "Draft invoice for this month's services",
        "detail": "Based on recent activity, drafted an invoice for this month's bookkeeping services. Review before it's created.",
        "build_payload": lambda: {
            "invoice_number": f"AUTO-{random.randint(1000, 9999)}",
            "amount_cents": random.choice([25000, 35000, 45000, 60000]),
            "due_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
        },
    },
]

# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    company_name: str = Field(min_length=1)
    name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=6)


class RegisterOfficerRequest(BaseModel):
    name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=6)
    company_name: Optional[str] = None  # optional solo-practice / trading name


class RegisterClientRequest(BaseModel):
    name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=6)
    officer_id: Optional[int] = None


class EmployeeCreateClientRequest(BaseModel):
    name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=6)


class CreateUserRequest(BaseModel):
    name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=6)
    role: str = Field(pattern="^(super_admin|admin|employee|client)$")
    assign_to_officer_id: Optional[int] = None  # only meaningful when role == "client"


class ModifyUserRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[str] = Field(default=None, pattern="^(super_admin|admin|employee|client)$")
    status: Optional[str] = Field(default=None, pattern="^(active|suspended)$")
    assign_to_officer_id: Optional[int] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6)


class InvoiceCreateRequest(BaseModel):
    client_id: int
    invoice_number: str = Field(min_length=1)
    amount_cents: int = Field(gt=0)
    due_date: str = Field(min_length=1)


class InvoiceUpdateRequest(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(unpaid|paid|overdue)$")
    due_date: Optional[str] = None


class ChecklistItemCreateRequest(BaseModel):
    client_id: int
    label: str = Field(min_length=1)


class MessageCreateRequest(BaseModel):
    client_id: int
    body: str = Field(min_length=1)


class ChaseRequest(BaseModel):
    client_id: int
    invoice_id: Optional[int] = None
    notes: Optional[str] = None


class CampaignStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|paused|completed)$")


class ScopeToggleRequest(BaseModel):
    enabled: bool


class GenerateSuggestionsRequest(BaseModel):
    client_id: int


class AssignmentRequest(BaseModel):
    employee_id: int
    client_id: int


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

@router.post("/api/auth/login")
def login(payload: LoginRequest):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (payload.email,)
        ).fetchone()

    if not user or not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    if user["status"] == "suspended":
        raise HTTPException(status_code=403, detail="This account has been suspended. Contact your administrator.")

    token = create_access_token(
        {
            "sub": str(user["id"]),
            "role": user["role"],
            "status": user["status"],
            "email": user["email"],
            "name": user["name"],
            "company_name": user["company_name"],
            "is_independent": bool(user["is_independent"]),
        }
    )
    log_audit(user["id"], user["role"], "login", f"{user['email']} logged in")

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "company_name": user["company_name"],
            "is_independent": bool(user["is_independent"]),
        },
    }


@router.post("/api/auth/register", status_code=201)
def register(payload: RegisterRequest):
    """
    Public self-service signup for a new firm. This is the only way an
    'admin' account gets created without an existing Super Admin or Admin
    inviting them -- it represents a brand-new firm coming onto the
    platform, so `created_by` is left NULL (self-registered) rather than
    pointing at another user. Every other account (Employee, Client, or an
    additional Admin) must still be created from inside the app by an
    existing Admin/Super Admin via /api/admin/users/create -- that rule is
    unchanged.
    """
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (payload.email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="An account with that email already exists")

        cursor = conn.execute(
            """INSERT INTO users (name, email, hashed_password, role, status, company_name, created_by)
               VALUES (?, ?, ?, 'admin', 'active', ?, NULL)""",
            (
                payload.name,
                payload.email,
                hash_password(payload.password),
                payload.company_name,
            ),
        )
        new_user_id = cursor.lastrowid

    log_audit(
        new_user_id,
        "admin",
        "self_register",
        f"New firm '{payload.company_name}' registered by {payload.email}",
    )

    token = create_access_token(
        {
            "sub": str(new_user_id),
            "role": "admin",
            "status": "active",
            "email": payload.email,
            "name": payload.name,
            "company_name": payload.company_name,
            "is_independent": False,
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": new_user_id,
            "name": payload.name,
            "email": payload.email,
            "role": "admin",
            "company_name": payload.company_name,
            "is_independent": False,
        },
    }


@router.post("/api/auth/register-officer", status_code=201)
def register_officer(payload: RegisterOfficerRequest):
    """
    Public self-service signup for an independent Account Officer (Employee)
    who has no firm/Admin above them. Mirrors /api/auth/register (the firm
    signup path) but creates an 'employee' account flagged is_independent=1,
    which unlocks POST /api/employee/clients/create -- the one place an
    Employee is allowed to create Client accounts themselves, scoped only to
    clients they create (auto-assigned to themselves as officer). A
    firm-invited Employee (is_independent=0) keeps today's behavior: their
    client roster is entirely managed by their firm's Admin.
    """
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="An account with that email already exists")

        cursor = conn.execute(
            """INSERT INTO users (name, email, hashed_password, role, status, company_name, is_independent, created_by)
               VALUES (?, ?, ?, 'employee', 'active', ?, 1, NULL)""",
            (payload.name, payload.email, hash_password(payload.password), payload.company_name),
        )
        new_user_id = cursor.lastrowid

    log_audit(new_user_id, "employee", "self_register_officer", f"Independent Account Officer registered: {payload.email}")

    token = create_access_token(
        {
            "sub": str(new_user_id),
            "role": "employee",
            "status": "active",
            "email": payload.email,
            "name": payload.name,
            "company_name": payload.company_name,
            "is_independent": True,
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": new_user_id,
            "name": payload.name,
            "email": payload.email,
            "role": "employee",
            "company_name": payload.company_name,
            "is_independent": True,
        },
    }


@router.get("/api/public/independent-officers")
def list_independent_officers():
    """
    Unauthenticated directory of independent Account Officers, so a Client
    self-registering without a firm can pick one during signup.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, name, company_name FROM users
               WHERE role = 'employee' AND is_independent = 1 AND status = 'active'
               ORDER BY name ASC"""
        ).fetchall()
    return {"officers": [dict(row) for row in rows]}


@router.post("/api/auth/register-client", status_code=201)
def register_client(payload: RegisterClientRequest):
    """
    Public self-service signup for a Client with no firm relationship yet.
    If officer_id is given it must reference an active, independent Employee
    -- Clients cannot self-assign to a firm-managed Employee, since that
    roster is controlled by the firm's Admin, not by public signup.
    """
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="An account with that email already exists")

        officer = None
        if payload.officer_id is not None:
            officer = conn.execute(
                "SELECT id FROM users WHERE id = ? AND role = 'employee' AND is_independent = 1 AND status = 'active'",
                (payload.officer_id,),
            ).fetchone()
            if not officer:
                raise HTTPException(status_code=400, detail="officer_id must reference an active independent Account Officer")

        cursor = conn.execute(
            """INSERT INTO users (name, email, hashed_password, role, status, created_by)
               VALUES (?, ?, ?, 'client', 'active', NULL)""",
            (payload.name, payload.email, hash_password(payload.password)),
        )
        new_user_id = cursor.lastrowid

        if officer:
            conn.execute(
                "INSERT INTO employee_client_assignments (employee_id, client_id) VALUES (?, ?)",
                (officer["id"], new_user_id),
            )

    log_audit(new_user_id, "client", "self_register_client",
              f"Client self-registered: {payload.email}" + (f" -> officer {payload.officer_id}" if officer else " (unassigned)"))

    token = create_access_token(
        {
            "sub": str(new_user_id),
            "role": "client",
            "status": "active",
            "email": payload.email,
            "name": payload.name,
            "company_name": None,
            "is_independent": False,
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": new_user_id,
            "name": payload.name,
            "email": payload.email,
            "role": "client",
            "company_name": None,
            "is_independent": False,
        },
    }


@router.get("/api/auth/me")
def me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.post("/api/auth/change-password")
def change_password(payload: ChangePasswordRequest, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (current_user["id"],)).fetchone()
        if not user or not verify_password(payload.current_password, user["hashed_password"]):
            raise HTTPException(status_code=401, detail="Current password is incorrect")

        conn.execute(
            "UPDATE users SET hashed_password = ? WHERE id = ?",
            (hash_password(payload.new_password), current_user["id"]),
        )

    log_audit(current_user["id"], current_user["role"], "change_password", "User changed their own password")
    return {"message": "Password updated successfully"}


# --------------------------------------------------------------------------
# User Management  (Super Admin + Admin only)
# --------------------------------------------------------------------------

@router.post("/api/admin/users/create", status_code=201)
def create_user(
    payload: CreateUserRequest,
    current_user: dict = Depends(require_role(["super_admin", "admin"])),
):
    # Admins (non-super) may not create other Admins or Super Admins -- only a
    # Super Admin can create accounts at admin rank or above.
    if payload.role in ("admin", "super_admin") and current_user["role"] != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Only a Super Admin may create new Admin or Super Admin accounts",
        )

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (payload.email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="A user with that email already exists")

        cursor = conn.execute(
            """INSERT INTO users (name, email, hashed_password, role, status, created_by)
               VALUES (?, ?, ?, ?, 'active', ?)""",
            (
                payload.name,
                payload.email,
                hash_password(payload.password),
                payload.role,
                current_user["id"],
            ),
        )
        new_user_id = cursor.lastrowid

        # Optional: assign a brand-new client straight to an officer at creation time.
        if payload.role == "client" and payload.assign_to_officer_id:
            officer = conn.execute(
                "SELECT id, role FROM users WHERE id = ?", (payload.assign_to_officer_id,)
            ).fetchone()
            if not officer or officer["role"] != "employee":
                raise HTTPException(status_code=400, detail="assign_to_officer_id must reference an Employee")
            conn.execute(
                "INSERT INTO employee_client_assignments (employee_id, client_id) VALUES (?, ?)",
                (payload.assign_to_officer_id, new_user_id),
            )

    log_audit(
        current_user["id"],
        current_user["role"],
        "create_user",
        f"Created {payload.role} account '{payload.email}' (id={new_user_id})",
    )
    return {"id": new_user_id, "message": "User created successfully"}


@router.get("/api/admin/users")
def list_users(current_user: dict = Depends(require_role(["super_admin", "admin"]))):
    with get_db() as conn:
        if current_user["role"] == "super_admin":
            rows = conn.execute(
                """SELECT id, name, email, role, status, created_by, created_at
                   FROM users ORDER BY created_at DESC"""
            ).fetchall()
        else:
            # Standard Admins only see Employees/Clients THEY personally created.
            rows = conn.execute(
                """SELECT id, name, email, role, status, created_by, created_at
                   FROM users
                   WHERE created_by = ? AND role IN ('employee', 'client')
                   ORDER BY created_at DESC""",
                (current_user["id"],),
            ).fetchall()

    return {"users": [dict(row) for row in rows]}


@router.patch("/api/admin/users/{user_id}")
def modify_user(
    user_id: int,
    payload: ModifyUserRequest,
    current_user: dict = Depends(require_role(["super_admin", "admin"])),
):
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        # Non-super Admins may only modify accounts they personally created,
        # and may never touch another Admin or a Super Admin.
        if current_user["role"] != "super_admin":
            if target["created_by"] != current_user["id"] or target["role"] in ("admin", "super_admin"):
                raise HTTPException(status_code=403, detail="You may only manage users you created")

        if target["role"] == "super_admin" and current_user["id"] != target["id"]:
            raise HTTPException(status_code=403, detail="Super Admin accounts cannot be modified by others")

        # Only a Super Admin may promote anyone to admin rank or above.
        if payload.role in ("admin", "super_admin") and current_user["role"] != "super_admin":
            raise HTTPException(status_code=403, detail="Only a Super Admin may assign Admin or Super Admin roles")

        fields, values = [], []
        for field in ("name", "email", "role", "status"):
            value = getattr(payload, field)
            if value is not None:
                fields.append(f"{field} = ?")
                values.append(value)

        if fields:
            values.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)

        if payload.assign_to_officer_id is not None:
            officer = conn.execute(
                "SELECT id, role FROM users WHERE id = ?", (payload.assign_to_officer_id,)
            ).fetchone()
            if not officer or officer["role"] != "employee":
                raise HTTPException(status_code=400, detail="assign_to_officer_id must reference an Employee")
            conn.execute("DELETE FROM employee_client_assignments WHERE client_id = ?", (user_id,))
            conn.execute(
                "INSERT INTO employee_client_assignments (employee_id, client_id) VALUES (?, ?)",
                (payload.assign_to_officer_id, user_id),
            )

    log_audit(current_user["id"], current_user["role"], "modify_user", f"Modified user id={user_id}: {payload.model_dump(exclude_none=True)}")
    return {"message": "User updated successfully"}


@router.post("/api/admin/users/{user_id}/revoke")
def revoke_access(user_id: int, current_user: dict = Depends(require_role(["super_admin", "admin"]))):
    """Suspends an account (soft-revoke, preserves audit history)."""
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target["role"] in ("admin", "super_admin") and current_user["role"] != "super_admin":
            raise HTTPException(status_code=403, detail="Only a Super Admin can revoke an Admin's access")
        if target["role"] == "super_admin":
            raise HTTPException(status_code=403, detail="Super Admin accounts cannot be revoked")

        conn.execute("UPDATE users SET status = 'suspended' WHERE id = ?", (user_id,))

    log_audit(current_user["id"], current_user["role"], "revoke_access", f"Suspended user id={user_id}")
    return {"message": "Access revoked"}


@router.post("/api/admin/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    payload: ResetPasswordRequest,
    current_user: dict = Depends(require_role(["super_admin", "admin"])),
):
    """
    Admin-initiated password reset (no current-password check, unlike
    /api/auth/change-password). Scoped identically to modify_user/
    revoke_access: a non-super Admin may only reset passwords for
    Employees/Clients they personally created, and nobody but a Super
    Admin themselves may reset a Super Admin's password.
    """
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        if current_user["role"] != "super_admin":
            if target["created_by"] != current_user["id"] or target["role"] in ("admin", "super_admin"):
                raise HTTPException(status_code=403, detail="You may only reset passwords for users you created")

        if target["role"] == "super_admin" and current_user["id"] != target["id"]:
            raise HTTPException(status_code=403, detail="Super Admin accounts cannot be modified by others")

        conn.execute(
            "UPDATE users SET hashed_password = ? WHERE id = ?",
            (hash_password(payload.new_password), user_id),
        )

    log_audit(current_user["id"], current_user["role"], "reset_password", f"Reset password for user id={user_id}")
    return {"message": "Password reset successfully"}


@router.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, current_user: dict = Depends(require_role(["super_admin"]))):
    """Hard delete -- reserved for Super Admin only, per spec."""
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target["role"] == "super_admin":
            raise HTTPException(status_code=403, detail="Cannot delete a Super Admin account")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    log_audit(current_user["id"], current_user["role"], "delete_user", f"Deleted user id={user_id}")
    return {"message": "User deleted"}


# --------------------------------------------------------------------------
# Employee <-> Client assignment management (Super Admin + Admin)
# --------------------------------------------------------------------------

@router.post("/api/assignments")
def assign_client_to_employee(
    payload: AssignmentRequest,
    current_user: dict = Depends(require_role(["super_admin", "admin"])),
):
    with get_db() as conn:
        employee = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'employee'", (payload.employee_id,)).fetchone()
        client = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'client'", (payload.client_id,)).fetchone()
        if not employee or not client:
            raise HTTPException(status_code=400, detail="Invalid employee_id or client_id")

        conn.execute("DELETE FROM employee_client_assignments WHERE client_id = ?", (payload.client_id,))
        conn.execute(
            "INSERT INTO employee_client_assignments (employee_id, client_id) VALUES (?, ?)",
            (payload.employee_id, payload.client_id),
        )

    log_audit(current_user["id"], current_user["role"], "assign_client",
              f"Client {payload.client_id} -> Employee {payload.employee_id}")
    return {"message": "Client assigned to officer"}


@router.get("/api/admin/audit")
def master_audit_log(current_user: dict = Depends(require_role(["super_admin", "admin"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
    return {"logs": [dict(row) for row in rows]}


# --------------------------------------------------------------------------
# Helper: resolve which client_ids an Employee is allowed to see
# --------------------------------------------------------------------------

def _clients_assigned_to_employee(conn, employee_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT client_id FROM employee_client_assignments WHERE employee_id = ?",
        (employee_id,),
    ).fetchall()
    return [row["client_id"] for row in rows]


def _assert_client_access(conn, current_user: dict, client_id: int) -> None:
    """
    Gate for every per-client domain endpoint (invoices, documents, checklist,
    messages, collections):
      - super_admin / admin -> firm-wide oversight, always allowed
      - employee            -> only if this client is assigned to them
      - client              -> only their own id
    """
    target = conn.execute(
        "SELECT id FROM users WHERE id = ? AND role = 'client'", (client_id,)
    ).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Client not found")

    if current_user["role"] in ("super_admin", "admin"):
        return

    if current_user["role"] == "employee":
        assigned = conn.execute(
            "SELECT 1 FROM employee_client_assignments WHERE employee_id = ? AND client_id = ?",
            (current_user["id"], client_id),
        ).fetchone()
        if not assigned:
            raise HTTPException(status_code=403, detail="This client is not assigned to you")
        return

    if current_user["role"] == "client":
        if current_user["id"] != client_id:
            raise HTTPException(status_code=403, detail="You may only access your own records")
        return

    raise HTTPException(status_code=403, detail="Not permitted")


async def _dispatch_composio_action(
    actor_id: int, actor_role: str, action_name: str, entity_id: str, params: Optional[dict] = None
) -> dict:
    """
    Server-side call-through to the Node automation layer. actor_id/actor_role
    are stamped from the already-verified JWT (current_user), never trusted
    from the browser, and automation.js independently re-validates them again
    before touching Composio -- defense in depth across both layers.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{AUTOMATION_BASE_URL}/api/v1/execute-action",
                headers={"X-Actor-Id": str(actor_id), "X-Actor-Role": actor_role},
                json={"action_name": action_name, "entity_id": entity_id, "params": params or {}},
            )
        return {"ok": resp.status_code < 400, "status_code": resp.status_code, "body": resp.json()}
    except httpx.RequestError as exc:
        return {"ok": False, "status_code": 502, "body": {"error": f"Automation layer unreachable: {exc}"}}


# --------------------------------------------------------------------------
# Scoped operational data (Smart Inbox / Data Cleaner)
# Employees only see rows for clients assigned to them.
# Clients are blocked entirely (403) -- they use the /api/client/* routes instead.
# Admin/Super Admin can see everything (oversight).
# --------------------------------------------------------------------------

@router.get("/api/dashboard/inbox")
def get_inbox(current_user: dict = Depends(get_current_user)):
    if current_user["role"] == "client":
        raise HTTPException(status_code=403, detail="Clients do not have access to the internal Smart Inbox")

    with get_db() as conn:
        if current_user["role"] == "employee":
            client_ids = _clients_assigned_to_employee(conn, current_user["id"])
            if not client_ids:
                return {"items": []}
            placeholders = ",".join("?" * len(client_ids))
            rows = conn.execute(
                f"""SELECT inbox_items.*, users.name AS client_name
                    FROM inbox_items JOIN users ON users.id = inbox_items.client_id
                    WHERE inbox_items.client_id IN ({placeholders})
                    ORDER BY inbox_items.created_at DESC""",
                client_ids,
            ).fetchall()
        else:  # admin / super_admin -> firm-wide visibility
            rows = conn.execute(
                """SELECT inbox_items.*, users.name AS client_name
                   FROM inbox_items JOIN users ON users.id = inbox_items.client_id
                   ORDER BY inbox_items.created_at DESC"""
            ).fetchall()

    return {"items": [dict(row) for row in rows]}


@router.get("/api/data/clean")
def get_data_clean_rows(current_user: dict = Depends(get_current_user)):
    if current_user["role"] == "client":
        raise HTTPException(status_code=403, detail="Clients do not have access to the Data Cleaner")

    with get_db() as conn:
        if current_user["role"] == "employee":
            client_ids = _clients_assigned_to_employee(conn, current_user["id"])
            if not client_ids:
                return {"rows": []}
            placeholders = ",".join("?" * len(client_ids))
            rows = conn.execute(
                f"""SELECT data_clean_rows.*, users.name AS client_name
                    FROM data_clean_rows JOIN users ON users.id = data_clean_rows.client_id
                    WHERE data_clean_rows.client_id IN ({placeholders})
                    ORDER BY data_clean_rows.created_at DESC""",
                client_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT data_clean_rows.*, users.name AS client_name
                   FROM data_clean_rows JOIN users ON users.id = data_clean_rows.client_id
                   ORDER BY data_clean_rows.created_at DESC"""
            ).fetchall()

    return {"rows": [dict(row) for row in rows]}


@router.post("/api/employee/clients/create", status_code=201)
def employee_create_client(
    payload: EmployeeCreateClientRequest,
    current_user: dict = Depends(require_role(["employee"])),
):
    """
    The one place an Employee can create a Client account themselves --
    restricted to independent Account Officers (is_independent=1), since a
    firm-invited Employee's client roster is managed by their Admin. The new
    client is auto-assigned to the creating officer.
    """
    if not current_user.get("is_independent"):
        raise HTTPException(
            status_code=403,
            detail="Only independent Account Officers can add their own clients directly. "
                   "Ask your firm's Admin to create client accounts.",
        )

    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="A user with that email already exists")

        cursor = conn.execute(
            """INSERT INTO users (name, email, hashed_password, role, status, created_by)
               VALUES (?, ?, ?, 'client', 'active', ?)""",
            (payload.name, payload.email, hash_password(payload.password), current_user["id"]),
        )
        new_client_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO employee_client_assignments (employee_id, client_id) VALUES (?, ?)",
            (current_user["id"], new_client_id),
        )

    log_audit(current_user["id"], current_user["role"], "employee_create_client",
              f"Created client '{payload.email}' (id={new_client_id})")
    return {"id": new_client_id, "message": "Client created and assigned to you"}


@router.get("/api/employee/overview")
def employee_overview(current_user: dict = Depends(require_role(["employee"]))):
    """At-a-glance counts for the Employee's own book -- roster size, unread
    inbox items, outstanding invoices, and active chaser campaigns."""
    with get_db() as conn:
        client_ids = _clients_assigned_to_employee(conn, current_user["id"])
        if not client_ids:
            return {
                "assigned_clients": 0,
                "unread_inbox": 0,
                "outstanding_invoices_count": 0,
                "outstanding_invoices_total_cents": 0,
                "active_campaigns": 0,
            }

        placeholders = ",".join("?" * len(client_ids))
        unread_inbox = conn.execute(
            f"SELECT COUNT(*) AS c FROM inbox_items WHERE client_id IN ({placeholders}) AND status = 'unread'",
            client_ids,
        ).fetchone()["c"]
        outstanding = conn.execute(
            f"""SELECT COUNT(*) AS c, COALESCE(SUM(amount_cents), 0) AS total FROM invoices
                WHERE client_id IN ({placeholders}) AND status IN ('unpaid', 'overdue')""",
            client_ids,
        ).fetchone()
        active_campaigns = conn.execute(
            f"SELECT COUNT(*) AS c FROM chaser_campaigns WHERE client_id IN ({placeholders}) AND status = 'active'",
            client_ids,
        ).fetchone()["c"]
        pending_suggestions = conn.execute(
            f"SELECT COUNT(*) AS c FROM ai_suggestions WHERE client_id IN ({placeholders}) AND status = 'pending'",
            client_ids,
        ).fetchone()["c"]

    return {
        "assigned_clients": len(client_ids),
        "unread_inbox": unread_inbox,
        "outstanding_invoices_count": outstanding["c"],
        "outstanding_invoices_total_cents": outstanding["total"],
        "active_campaigns": active_campaigns,
        "pending_ai_suggestions": pending_suggestions,
    }


@router.get("/api/dashboard/assigned-clients")
def get_assigned_clients(current_user: dict = Depends(require_role(["employee", "admin", "super_admin"]))):
    """Employee's own client roster (used to populate the 'Assigned Clients' nav view)."""
    with get_db() as conn:
        if current_user["role"] == "employee":
            rows = conn.execute(
                """SELECT users.id, users.name, users.email, users.status
                   FROM employee_client_assignments
                   JOIN users ON users.id = employee_client_assignments.client_id
                   WHERE employee_client_assignments.employee_id = ?""",
                (current_user["id"],),
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, name, email, status FROM users WHERE role = 'client'").fetchall()
    return {"clients": [dict(row) for row in rows]}


# --------------------------------------------------------------------------
# Invoices -- Employee/Admin/Super Admin create & manage, scoped by client.
# --------------------------------------------------------------------------

@router.get("/api/dashboard/invoices")
def list_dashboard_invoices(
    client_id: Optional[int] = None,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        if client_id is not None:
            _assert_client_access(conn, current_user, client_id)
            rows = conn.execute(
                """SELECT invoices.*, users.name AS client_name
                   FROM invoices JOIN users ON users.id = invoices.client_id
                   WHERE invoices.client_id = ? ORDER BY invoices.created_at DESC""",
                (client_id,),
            ).fetchall()
        elif current_user["role"] == "employee":
            client_ids = _clients_assigned_to_employee(conn, current_user["id"])
            if not client_ids:
                return {"invoices": []}
            placeholders = ",".join("?" * len(client_ids))
            rows = conn.execute(
                f"""SELECT invoices.*, users.name AS client_name
                    FROM invoices JOIN users ON users.id = invoices.client_id
                    WHERE invoices.client_id IN ({placeholders})
                    ORDER BY invoices.created_at DESC""",
                client_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT invoices.*, users.name AS client_name
                   FROM invoices JOIN users ON users.id = invoices.client_id
                   ORDER BY invoices.created_at DESC"""
            ).fetchall()
    return {"invoices": [dict(row) for row in rows]}


@router.post("/api/dashboard/invoices", status_code=201)
def create_invoice(
    payload: InvoiceCreateRequest,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        _assert_client_access(conn, current_user, payload.client_id)
        cursor = conn.execute(
            """INSERT INTO invoices (client_id, invoice_number, amount_cents, due_date, created_by)
               VALUES (?, ?, ?, ?, ?)""",
            (payload.client_id, payload.invoice_number, payload.amount_cents, payload.due_date, current_user["id"]),
        )
        new_id = cursor.lastrowid

    log_audit(current_user["id"], current_user["role"], "create_invoice",
              f"Invoice {payload.invoice_number} for client {payload.client_id}")
    return {"id": new_id, "message": "Invoice created"}


@router.patch("/api/dashboard/invoices/{invoice_id}")
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdateRequest,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
        _assert_client_access(conn, current_user, invoice["client_id"])

        fields, values = [], []
        if payload.status is not None:
            fields.append("status = ?")
            values.append(payload.status)
            if payload.status == "paid":
                fields.append("paid_at = datetime('now')")
        if payload.due_date is not None:
            fields.append("due_date = ?")
            values.append(payload.due_date)
        if fields:
            values.append(invoice_id)
            conn.execute(f"UPDATE invoices SET {', '.join(fields)} WHERE id = ?", values)

    log_audit(current_user["id"], current_user["role"], "update_invoice", f"Invoice id={invoice_id}: {payload.model_dump(exclude_none=True)}")
    return {"message": "Invoice updated"}


# --------------------------------------------------------------------------
# Documents -- Client uploads, Employee/Admin/Super Admin view (scoped).
# --------------------------------------------------------------------------

@router.get("/api/dashboard/documents")
def list_dashboard_documents(
    client_id: Optional[int] = None,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        if client_id is not None:
            _assert_client_access(conn, current_user, client_id)
            rows = conn.execute(
                """SELECT documents.*, users.name AS client_name
                   FROM documents JOIN users ON users.id = documents.client_id
                   WHERE documents.client_id = ? ORDER BY documents.created_at DESC""",
                (client_id,),
            ).fetchall()
        elif current_user["role"] == "employee":
            client_ids = _clients_assigned_to_employee(conn, current_user["id"])
            if not client_ids:
                return {"documents": []}
            placeholders = ",".join("?" * len(client_ids))
            rows = conn.execute(
                f"""SELECT documents.*, users.name AS client_name
                    FROM documents JOIN users ON users.id = documents.client_id
                    WHERE documents.client_id IN ({placeholders})
                    ORDER BY documents.created_at DESC""",
                client_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT documents.*, users.name AS client_name
                   FROM documents JOIN users ON users.id = documents.client_id
                   ORDER BY documents.created_at DESC"""
            ).fetchall()
    return {"documents": [dict(row) for row in rows]}


@router.get("/api/dashboard/documents/{document_id}/download")
def download_dashboard_document(
    document_id: int,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        _assert_client_access(conn, current_user, doc["client_id"])

    file_path = UPLOAD_DIR / doc["stored_filename"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing from storage")
    return FileResponse(file_path, filename=doc["original_name"], media_type=doc["content_type"] or "application/octet-stream")


# --------------------------------------------------------------------------
# Checklist -- Employee/Admin/Super Admin assign items, Client checks them off.
# --------------------------------------------------------------------------

@router.get("/api/dashboard/checklist")
def list_dashboard_checklist(
    client_id: int,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        _assert_client_access(conn, current_user, client_id)
        rows = conn.execute(
            "SELECT * FROM checklist_items WHERE client_id = ? ORDER BY created_at DESC", (client_id,)
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.post("/api/dashboard/checklist", status_code=201)
def create_checklist_item(
    payload: ChecklistItemCreateRequest,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        _assert_client_access(conn, current_user, payload.client_id)
        cursor = conn.execute(
            "INSERT INTO checklist_items (client_id, label, created_by) VALUES (?, ?, ?)",
            (payload.client_id, payload.label, current_user["id"]),
        )
        new_id = cursor.lastrowid

    log_audit(current_user["id"], current_user["role"], "create_checklist_item",
              f"'{payload.label}' for client {payload.client_id}")
    return {"id": new_id, "message": "Checklist item added"}


@router.delete("/api/dashboard/checklist/{item_id}")
def delete_checklist_item(
    item_id: int,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        item = conn.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Checklist item not found")
        _assert_client_access(conn, current_user, item["client_id"])
        conn.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))

    log_audit(current_user["id"], current_user["role"], "delete_checklist_item", f"item id={item_id}")
    return {"message": "Checklist item removed"}


# --------------------------------------------------------------------------
# Messaging -- shared thread per client, scoped identically to the domain
# tables above. Powers both the Client's "Contact Officer" view and the
# Employee's per-client message action in "Assigned Clients".
# --------------------------------------------------------------------------

@router.get("/api/messages/{client_id}")
def get_message_thread(client_id: int, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        _assert_client_access(conn, current_user, client_id)
        rows = conn.execute(
            """SELECT messages.*, users.name AS sender_name
               FROM messages JOIN users ON users.id = messages.sender_id
               WHERE messages.client_id = ? ORDER BY messages.created_at ASC""",
            (client_id,),
        ).fetchall()
    return {"messages": [dict(row) for row in rows]}


@router.post("/api/messages", status_code=201)
def send_message(payload: MessageCreateRequest, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        _assert_client_access(conn, current_user, payload.client_id)
        cursor = conn.execute(
            "INSERT INTO messages (client_id, sender_id, sender_role, body) VALUES (?, ?, ?, ?)",
            (payload.client_id, current_user["id"], current_user["role"], payload.body),
        )
        new_id = cursor.lastrowid

    log_audit(current_user["id"], current_user["role"], "send_message", f"to client thread {payload.client_id}")
    return {"id": new_id, "message": "Message sent"}


# --------------------------------------------------------------------------
# Collections / Chaser Campaigns -- Employee (assigned clients) + Admin/Super
# Admin oversight. Sending a chaser posts a message to the client thread too.
# --------------------------------------------------------------------------

@router.get("/api/dashboard/collections")
def list_collections(current_user: dict = Depends(require_role(["employee", "admin", "super_admin"]))):
    with get_db() as conn:
        if current_user["role"] == "employee":
            client_ids = _clients_assigned_to_employee(conn, current_user["id"])
            if not client_ids:
                return {"campaigns": [], "overdue_invoices": []}
            placeholders = ",".join("?" * len(client_ids))
            campaigns = conn.execute(
                f"""SELECT chaser_campaigns.*, users.name AS client_name
                    FROM chaser_campaigns JOIN users ON users.id = chaser_campaigns.client_id
                    WHERE chaser_campaigns.client_id IN ({placeholders})
                    ORDER BY chaser_campaigns.created_at DESC""",
                client_ids,
            ).fetchall()
            overdue = conn.execute(
                f"""SELECT invoices.*, users.name AS client_name
                    FROM invoices JOIN users ON users.id = invoices.client_id
                    WHERE invoices.client_id IN ({placeholders}) AND invoices.status IN ('unpaid', 'overdue')
                    ORDER BY invoices.due_date ASC""",
                client_ids,
            ).fetchall()
        else:
            campaigns = conn.execute(
                """SELECT chaser_campaigns.*, users.name AS client_name
                   FROM chaser_campaigns JOIN users ON users.id = chaser_campaigns.client_id
                   ORDER BY chaser_campaigns.created_at DESC"""
            ).fetchall()
            overdue = conn.execute(
                """SELECT invoices.*, users.name AS client_name
                   FROM invoices JOIN users ON users.id = invoices.client_id
                   WHERE invoices.status IN ('unpaid', 'overdue')
                   ORDER BY invoices.due_date ASC"""
            ).fetchall()

    return {
        "campaigns": [dict(row) for row in campaigns],
        "overdue_invoices": [dict(row) for row in overdue],
    }


@router.post("/api/dashboard/collections/chase", status_code=201)
def send_chaser(payload: ChaseRequest, current_user: dict = Depends(require_role(["employee", "admin", "super_admin"]))):
    with get_db() as conn:
        _assert_client_access(conn, current_user, payload.client_id)

        # Employees own the campaign as themselves; Admin/Super Admin acting
        # on a client without an assigned officer simply log the campaign
        # under their own id so the trail always names a real actor.
        employee_id = current_user["id"]
        if current_user["role"] != "employee":
            officer = conn.execute(
                "SELECT employee_id FROM employee_client_assignments WHERE client_id = ?",
                (payload.client_id,),
            ).fetchone()
            if officer:
                employee_id = officer["employee_id"]

        campaign = conn.execute(
            "SELECT * FROM chaser_campaigns WHERE client_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (payload.client_id,),
        ).fetchone()

        if campaign:
            conn.execute(
                "UPDATE chaser_campaigns SET last_chased_at = datetime('now'), notes = ?, invoice_id = ? WHERE id = ?",
                (payload.notes, payload.invoice_id, campaign["id"]),
            )
            campaign_id = campaign["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO chaser_campaigns (client_id, employee_id, invoice_id, notes, last_chased_at)
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                (payload.client_id, employee_id, payload.invoice_id, payload.notes),
            )
            campaign_id = cursor.lastrowid

        chaser_note = payload.notes or "Friendly reminder: you have an outstanding invoice. Please arrange payment at your earliest convenience."
        conn.execute(
            "INSERT INTO messages (client_id, sender_id, sender_role, body) VALUES (?, ?, ?, ?)",
            (payload.client_id, current_user["id"], current_user["role"], chaser_note),
        )

    log_audit(current_user["id"], current_user["role"], "send_chaser", f"Chased client {payload.client_id}")
    return {"id": campaign_id, "message": "Chaser sent"}


@router.patch("/api/dashboard/collections/campaigns/{campaign_id}")
def update_campaign_status(
    campaign_id: int,
    payload: CampaignStatusRequest,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        campaign = conn.execute("SELECT * FROM chaser_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        _assert_client_access(conn, current_user, campaign["client_id"])
        conn.execute("UPDATE chaser_campaigns SET status = ? WHERE id = ?", (payload.status, campaign_id))

    log_audit(current_user["id"], current_user["role"], "update_campaign_status", f"Campaign id={campaign_id} -> {payload.status}")
    return {"message": "Campaign updated"}


# --------------------------------------------------------------------------
# Composio Scopes -- Admin + Super Admin configure connector scopes. Toggling
# a scope calls through to the Node automation layer's execute-action
# endpoint so the same admin-rank whitelist enforced there (Part 3) is
# exercised end-to-end, not just re-implemented locally.
# --------------------------------------------------------------------------

@router.get("/api/admin/composio-scopes")
def list_composio_scopes(current_user: dict = Depends(require_role(["admin", "super_admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM composio_scopes ORDER BY high_level DESC, label ASC").fetchall()
    return {"scopes": [dict(row) for row in rows]}


@router.post("/api/admin/composio-scopes/{scope_key}/toggle")
async def toggle_composio_scope(
    scope_key: str,
    payload: ScopeToggleRequest,
    current_user: dict = Depends(require_role(["admin", "super_admin"])),
):
    with get_db() as conn:
        scope = conn.execute("SELECT * FROM composio_scopes WHERE scope_key = ?", (scope_key,)).fetchone()
        if not scope:
            raise HTTPException(status_code=404, detail="Unknown scope")

        dispatch = await _dispatch_composio_action(
            actor_id=current_user["id"],
            actor_role=current_user["role"],
            action_name=f"update_scope_{scope_key}",
            entity_id=current_user.get("company_name") or f"user-{current_user['id']}",
            params={"enabled": payload.enabled},
        )

        conn.execute(
            "UPDATE composio_scopes SET enabled = ?, updated_by = ?, updated_at = datetime('now') WHERE scope_key = ?",
            (1 if payload.enabled else 0, current_user["id"], scope_key),
        )

    log_audit(
        current_user["id"], current_user["role"], "toggle_composio_scope",
        f"{scope_key} -> {'enabled' if payload.enabled else 'disabled'} (automation: {dispatch['status_code']})",
    )
    return {
        "message": "Scope updated",
        "enabled": payload.enabled,
        "automation_dispatch": dispatch,
    }


# --------------------------------------------------------------------------
# AI Suggestions -- simulated agent output, real human-in-the-loop review.
# Employee/Admin/Super Admin only (internal working tool, not client-facing).
# --------------------------------------------------------------------------

@router.post("/api/dashboard/ai-suggestions/generate", status_code=201)
def generate_ai_suggestions(
    payload: GenerateSuggestionsRequest,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        _assert_client_access(conn, current_user, payload.client_id)

        picks = random.sample(AI_SUGGESTION_TEMPLATES, k=min(2, len(AI_SUGGESTION_TEMPLATES)))
        new_ids = []
        for tpl in picks:
            cursor = conn.execute(
                """INSERT INTO ai_suggestions (client_id, suggestion_type, title, detail, payload)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    payload.client_id,
                    tpl["suggestion_type"],
                    tpl["title"],
                    tpl["detail"],
                    json.dumps(tpl["build_payload"]()),
                ),
            )
            new_ids.append(cursor.lastrowid)

    log_audit(current_user["id"], current_user["role"], "generate_ai_suggestions",
              f"Generated {len(new_ids)} suggestion(s) for client {payload.client_id}")
    return {"ids": new_ids, "message": f"{len(new_ids)} suggestion(s) generated"}


@router.get("/api/dashboard/ai-suggestions")
def list_ai_suggestions(
    client_id: int,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        _assert_client_access(conn, current_user, client_id)
        rows = conn.execute(
            "SELECT * FROM ai_suggestions WHERE client_id = ? ORDER BY generated_at DESC", (client_id,)
        ).fetchall()
    return {"suggestions": [dict(row) for row in rows]}


@router.post("/api/dashboard/ai-suggestions/{suggestion_id}/approve")
async def approve_ai_suggestion(
    suggestion_id: int,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        suggestion = conn.execute("SELECT * FROM ai_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if not suggestion:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        _assert_client_access(conn, current_user, suggestion["client_id"])
        if suggestion["status"] != "pending":
            raise HTTPException(status_code=400, detail="Suggestion has already been reviewed")

        payload = json.loads(suggestion["payload"])

        # Re-validated through the same actor-role-gated dispatcher used for
        # Composio actions (Part 3) -- approving an AI suggestion is itself a
        # sensitive action, and gets the same permanent audit trail.
        dispatch = await _dispatch_composio_action(
            actor_id=current_user["id"],
            actor_role=current_user["role"],
            action_name=f"ai_approve_{suggestion['suggestion_type']}",
            entity_id=f"client-{suggestion['client_id']}",
            params=payload,
        )

        if suggestion["suggestion_type"] == "reconciliation_flag":
            conn.execute(
                "INSERT INTO data_clean_rows (client_id, row_label, flag) VALUES (?, ?, ?)",
                (suggestion["client_id"], payload["row_label"], payload["flag"]),
            )
        elif suggestion["suggestion_type"] == "client_reminder":
            conn.execute(
                "INSERT INTO messages (client_id, sender_id, sender_role, body) VALUES (?, ?, ?, ?)",
                (suggestion["client_id"], current_user["id"], current_user["role"], payload["body"]),
            )
        elif suggestion["suggestion_type"] == "invoice_draft":
            conn.execute(
                """INSERT INTO invoices (client_id, invoice_number, amount_cents, due_date, created_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (suggestion["client_id"], payload["invoice_number"], payload["amount_cents"], payload["due_date"], current_user["id"]),
            )

        conn.execute(
            "UPDATE ai_suggestions SET status = 'approved', reviewed_by = ?, reviewed_at = datetime('now') WHERE id = ?",
            (current_user["id"], suggestion_id),
        )

    log_audit(
        current_user["id"], current_user["role"], "approve_ai_suggestion",
        f"Approved {suggestion['suggestion_type']} suggestion id={suggestion_id} (automation: {dispatch['status_code']})",
    )
    return {"message": "Suggestion approved and applied"}


@router.post("/api/dashboard/ai-suggestions/{suggestion_id}/reject")
def reject_ai_suggestion(
    suggestion_id: int,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        suggestion = conn.execute("SELECT * FROM ai_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if not suggestion:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        _assert_client_access(conn, current_user, suggestion["client_id"])
        if suggestion["status"] != "pending":
            raise HTTPException(status_code=400, detail="Suggestion has already been reviewed")

        conn.execute(
            "UPDATE ai_suggestions SET status = 'rejected', reviewed_by = ?, reviewed_at = datetime('now') WHERE id = ?",
            (current_user["id"], suggestion_id),
        )

    log_audit(current_user["id"], current_user["role"], "reject_ai_suggestion", f"Rejected suggestion id={suggestion_id}")
    return {"message": "Suggestion rejected"}


# --------------------------------------------------------------------------
# AI Report -- real Gemini call narrating a client's actual data (Tier 0:
# no Composio connector or bank feed dependency, purely a summarizer over
# data already in this database).
# --------------------------------------------------------------------------

@router.get("/api/dashboard/reports/client-summary")
def client_ai_report(
    client_id: int,
    current_user: dict = Depends(require_role(["employee", "admin", "super_admin"])),
):
    with get_db() as conn:
        _assert_client_access(conn, current_user, client_id)
        client = conn.execute("SELECT name FROM users WHERE id = ?", (client_id,)).fetchone()

        invoices = conn.execute(
            "SELECT invoice_number, amount_cents, status, due_date FROM invoices WHERE client_id = ?", (client_id,)
        ).fetchall()
        checklist_items = conn.execute(
            "SELECT label, is_complete FROM checklist_items WHERE client_id = ?", (client_id,)
        ).fetchall()
        messages = conn.execute(
            "SELECT body, created_at, sender_role FROM messages WHERE client_id = ? ORDER BY created_at DESC LIMIT 5",
            (client_id,),
        ).fetchall()
        campaigns = conn.execute(
            "SELECT status, notes, last_chased_at FROM chaser_campaigns WHERE client_id = ?", (client_id,)
        ).fetchall()

        api_key, model_name = _get_gemini_config(conn)

    summary_text = _generate_client_report_text(
        client["name"],
        [dict(r) for r in invoices],
        [dict(r) for r in checklist_items],
        [dict(r) for r in messages],
        [dict(r) for r in campaigns],
        api_key,
        model_name,
    )

    log_audit(current_user["id"], current_user["role"], "generate_ai_report", f"Generated AI report for client {client_id}")
    return {"summary": summary_text}


# --------------------------------------------------------------------------
# Super Admin: Gemini API key/model configuration (runtime setting, not env var)
# --------------------------------------------------------------------------

class GeminiSettingsRequest(BaseModel):
    api_key: str = Field(min_length=1)
    model: Optional[str] = None


@router.get("/api/admin/settings/gemini")
def get_gemini_settings(current_user: dict = Depends(require_role(["super_admin"]))):
    with get_db() as conn:
        key_row = conn.execute("SELECT value, updated_at FROM app_settings WHERE key = 'gemini_api_key'").fetchone()
        model = _get_app_setting(conn, "gemini_model") or GEMINI_MODEL_DEFAULT

    configured = bool(key_row and key_row["value"])
    return {
        "configured": configured,
        "masked_key": f"••••••••{key_row['value'][-4:]}" if configured else None,
        "model": model,
        "updated_at": key_row["updated_at"] if key_row else None,
    }


@router.post("/api/admin/settings/gemini")
def set_gemini_settings(
    payload: GeminiSettingsRequest,
    current_user: dict = Depends(require_role(["super_admin"])),
):
    with get_db() as conn:
        _set_app_setting(conn, "gemini_api_key", payload.api_key, current_user["id"])
        if payload.model:
            _set_app_setting(conn, "gemini_model", payload.model, current_user["id"])

    log_audit(current_user["id"], current_user["role"], "update_gemini_settings", "Updated Gemini API key/model")
    return {"message": "Gemini settings updated"}


# --------------------------------------------------------------------------
# Client-only self-service routes (Upload Center / Invoices / Contact Officer)
# --------------------------------------------------------------------------

@router.get("/api/client/overview")
def client_overview(current_user: dict = Depends(require_role(["client"]))):
    with get_db() as conn:
        officer = conn.execute(
            """SELECT users.id, users.name, users.email
               FROM employee_client_assignments
               JOIN users ON users.id = employee_client_assignments.employee_id
               WHERE employee_client_assignments.client_id = ?""",
            (current_user["id"],),
        ).fetchone()
        inbox_count = conn.execute(
            "SELECT COUNT(*) AS c FROM inbox_items WHERE client_id = ?", (current_user["id"],)
        ).fetchone()["c"]
        invoice_stats = conn.execute(
            """SELECT COUNT(*) AS c, COALESCE(SUM(amount_cents), 0) AS total FROM invoices
               WHERE client_id = ? AND status IN ('unpaid', 'overdue')""",
            (current_user["id"],),
        ).fetchone()
        checklist_stats = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(is_complete), 0) AS complete FROM checklist_items WHERE client_id = ?",
            (current_user["id"],),
        ).fetchone()
        latest_message = conn.execute(
            """SELECT messages.*, users.name AS sender_name
               FROM messages JOIN users ON users.id = messages.sender_id
               WHERE messages.client_id = ? ORDER BY messages.created_at DESC LIMIT 1""",
            (current_user["id"],),
        ).fetchone()

    return {
        "assigned_officer": dict(officer) if officer else None,
        "open_items": inbox_count,
        "unpaid_invoices_count": invoice_stats["c"],
        "unpaid_invoices_total_cents": invoice_stats["total"],
        "checklist_total": checklist_stats["total"],
        "checklist_complete": checklist_stats["complete"],
        "latest_message": dict(latest_message) if latest_message else None,
    }


@router.get("/api/client/invoices")
def client_list_invoices(current_user: dict = Depends(require_role(["client"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM invoices WHERE client_id = ? ORDER BY created_at DESC", (current_user["id"],)
        ).fetchall()
    return {"invoices": [dict(row) for row in rows]}


@router.post("/api/client/invoices/{invoice_id}/pay")
def client_pay_invoice(invoice_id: int, current_user: dict = Depends(require_role(["client"]))):
    """Simulated payment -- marks the invoice paid immediately, no real processor wired up."""
    with get_db() as conn:
        invoice = conn.execute(
            "SELECT * FROM invoices WHERE id = ? AND client_id = ?", (invoice_id, current_user["id"])
        ).fetchone()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
        if invoice["status"] == "paid":
            raise HTTPException(status_code=400, detail="Invoice is already paid")

        conn.execute(
            "UPDATE invoices SET status = 'paid', paid_at = datetime('now') WHERE id = ?", (invoice_id,)
        )

    log_audit(current_user["id"], current_user["role"], "pay_invoice", f"Invoice id={invoice_id}")
    return {"message": "Payment recorded"}


@router.get("/api/client/checklist")
def client_list_checklist(current_user: dict = Depends(require_role(["client"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM checklist_items WHERE client_id = ? ORDER BY created_at DESC", (current_user["id"],)
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.patch("/api/client/checklist/{item_id}/toggle")
def client_toggle_checklist(item_id: int, current_user: dict = Depends(require_role(["client"]))):
    with get_db() as conn:
        item = conn.execute(
            "SELECT * FROM checklist_items WHERE id = ? AND client_id = ?", (item_id, current_user["id"])
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Checklist item not found")

        new_state = 0 if item["is_complete"] else 1
        if new_state:
            conn.execute(
                "UPDATE checklist_items SET is_complete = 1, completed_at = datetime('now') WHERE id = ?",
                (item_id,),
            )
        else:
            conn.execute(
                "UPDATE checklist_items SET is_complete = 0, completed_at = NULL WHERE id = ?",
                (item_id,),
            )

    return {"message": "Checklist item updated", "is_complete": bool(new_state)}


@router.get("/api/client/documents")
def client_list_documents(current_user: dict = Depends(require_role(["client"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE client_id = ? ORDER BY created_at DESC", (current_user["id"],)
        ).fetchall()
    return {"documents": [dict(row) for row in rows]}


@router.post("/api/client/documents", status_code=201)
async def client_upload_document(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role(["client"])),
):
    stored_filename = f"{current_user['id']}_{uuid.uuid4().hex}_{file.filename}"
    destination = UPLOAD_DIR / stored_filename

    contents = await file.read()
    destination.write_bytes(contents)

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO documents (client_id, stored_filename, original_name, content_type, size_bytes, uploaded_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (current_user["id"], stored_filename, file.filename, file.content_type, len(contents), current_user["id"]),
        )
        new_id = cursor.lastrowid

    log_audit(current_user["id"], current_user["role"], "upload_document", file.filename)
    return {"id": new_id, "message": "Document uploaded"}


@router.delete("/api/client/documents/{document_id}")
def client_delete_document(document_id: int, current_user: dict = Depends(require_role(["client"]))):
    with get_db() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND client_id = ?", (document_id, current_user["id"])
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    file_path = UPLOAD_DIR / doc["stored_filename"]
    if file_path.exists():
        file_path.unlink()

    return {"message": "Document removed"}


@router.get("/api/dashboard/global-analytics")
def global_analytics(current_user: dict = Depends(require_role(["super_admin"]))):
    """Firm-wide billing/analytics -- Super Admin exclusive, per spec."""
    with get_db() as conn:
        counts = conn.execute(
            "SELECT role, COUNT(*) AS c FROM users GROUP BY role"
        ).fetchall()
    return {"user_counts": {row["role"]: row["c"] for row in counts}}
