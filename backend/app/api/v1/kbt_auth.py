"""
TBAPS — KBT Executable Auth Endpoints
POST   /api/v1/kbt/auto-login          → Employee auto-authenticates using embedded token
POST   /api/v1/kbt/activate            → Employee activates KBT on first run (one-time code)
GET    /api/v1/kbt/token/{id}          → Admin: generate / rotate a KBT token for an employee
DELETE /api/v1/kbt/revoke/{id}         → Admin: revoke KBT token (blocks executable)

Security model:
  - Each employee has a unique raw_token stored ONLY in their KBT binary
  - Backend stores only SHA-256(raw_token) → a DB breach cannot reconstruct tokens
  - Auto-login issues a short-lived session JWT (12 h, same as manual login)
  - Token can be revoked instantly by clearing kbt_token_hash in DB
  - Activation code: 6-char alphanumeric, one-time use, stored as SHA-256 hash
  - KBT auto-login is blocked until activation_status = 'activated'
"""

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.api.v1.employee_auth import _make_employee_token
from app.api.v1.rbac_auth import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kbt", tags=["kbt-auth"])

_TOKEN_BYTES    = 48          # 384-bit token → urlsafe_b64 = 64 chars
_TOKEN_TTL_DAYS = 365         # KBT tokens are long-lived; rotation via revoke+regenerate


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token string."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_raw_token() -> str:
    """Cryptographically secure 48-byte URL-safe token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


async def _notify_staff_of_activation(
    db: AsyncSession,
    employee_name: str,
    department: str,
    activation_ts: str,
) -> None:
    """
    Fire-and-forget: send activation notification emails to:
      - The manager assigned to the employee's department
      - All HR users (organisation-wide)

    Manager receives notification only for their department.
    HR receives notification for all employees.
    """
    import asyncio
    from app.services.email_service import send_activation_notification_email

    try:
        # Fetch manager(s) for this department
        mgr_result = await db.execute(
            text("""
                SELECT su.email, su.username AS name
                FROM system_users su
                JOIN departments d ON su.department_id = d.id
                WHERE su.role = 'manager'
                  AND su.is_active = TRUE
                  AND d.name ILIKE :dept
            """),
            {"dept": f"%{department}%"},
        )
        managers = mgr_result.fetchall()

        # Fetch all HR users
        hr_result = await db.execute(
            text("""
                SELECT email, username AS name
                FROM system_users
                WHERE role = 'hr' AND is_active = TRUE
            """)
        )
        hr_users = hr_result.fetchall()

        staff_to_notify = list(managers) + list(hr_users)

        for row in staff_to_notify:
            asyncio.create_task(
                send_activation_notification_email(
                    to_email=row.email,
                    to_name=row.name,
                    employee_name=employee_name,
                    department=department,
                    activation_ts=activation_ts,
                )
            )

        logger.info(
            f"[kbt-activate] Queued activation notification to "
            f"{len(managers)} manager(s) + {len(hr_users)} HR user(s)"
        )
    except Exception as e:
        logger.warning(f"[kbt-activate] Failed to queue staff notifications: {e}")


# ── Request / Response models ──────────────────────────────────────────────────

class AutoLoginRequest(BaseModel):
    employee_id: str
    token:       str


class AutoLoginResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int = 12 * 3600
    employee_id:  str
    name:         str
    email:        str
    department:   Optional[str] = None
    role:         Optional[str] = None


class ActivateRequest(BaseModel):
    employee_id:     str
    activation_code: str
    device_info:     Optional[str] = None


class ActivateResponse(BaseModel):
    ok:           bool
    message:      str
    access_token: Optional[str] = None
    token_type:   str = "bearer"
    expires_in:   int = 12 * 3600


class KBTTokenResponse(BaseModel):
    employee_id:  str
    token:        str          # raw token — shown ONCE, then discarded
    api_url:      str
    generated_at: str
    expires_at:   str


# ── POST /kbt/activate ─────────────────────────────────────────────────────────

@router.post("/activate", response_model=ActivateResponse)
async def kbt_activate(
    body: ActivateRequest,
    db:   AsyncSession = Depends(get_db),
):
    """
    First-time activation endpoint for the KBT Executable.

    The employee is prompted once (on first run) to enter their activation code.
    After successful activation:
      - activation_status is set to 'activated'
      - activation_code_hash is cleared (one-time use)
      - A session JWT is issued
      - Manager + HR are notified

    Subsequent auto-login calls proceed without any code prompt.
    """
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired activation code. Contact your administrator.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Fetch employee row
    result = await db.execute(
        text("""
            SELECT e.id::text             AS id,
                   e.email,
                   e.name,
                   e.department,
                   e.role,
                   e.status,
                   e.activation_status,
                   e.activation_code_hash,
                   e.activation_code_expires_at
            FROM   employees e
            WHERE  e.id::text = :emp_id
              AND  e.deleted_at IS NULL
        """),
        {"emp_id": body.employee_id},
    )
    row = result.fetchone()

    if not row:
        logger.warning(f"[kbt-activate] employee_id not found: {body.employee_id}")
        raise invalid_exc

    if row.status != "active":
        logger.warning(f"[kbt-activate] employee inactive: {body.employee_id}")
        raise invalid_exc

    # Already activated — idempotent success
    if row.activation_status == "activated":
        logger.info(f"[kbt-activate] already activated: {body.employee_id}")
        token = _make_employee_token(row.id, row.email, row.name)
        return ActivateResponse(
            ok=True,
            message="KBT already activated. System initialised.",
            access_token=token,
        )

    # Validate activation code exists and is not expired
    if not row.activation_code_hash:
        logger.warning(f"[kbt-activate] no activation code for: {body.employee_id}")
        raise invalid_exc

    if row.activation_code_expires_at:
        exp = row.activation_code_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            logger.warning(f"[kbt-activate] activation code expired for: {body.employee_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Activation code has expired. Contact your administrator for a new onboarding email.",
            )

    # Constant-time hash comparison
    incoming_hash = _hash_token(body.activation_code.strip().upper())
    if not secrets.compare_digest(incoming_hash, row.activation_code_hash):
        logger.warning(f"[kbt-activate] invalid activation code for: {body.employee_id}")
        # Write failed activation audit log
        try:
            await db.execute(
                text("""
                    INSERT INTO audit_logs (action, resource_type, resource_id, changes)
                    VALUES ('activation_failed', 'kbt_activation', :emp_id, CAST(:changes AS JSONB))
                """),
                {
                    "emp_id": body.employee_id,
                    "changes": json.dumps({
                        "employee_id": body.employee_id,
                        "device_info": body.device_info,
                        "timestamp":   datetime.now(timezone.utc).isoformat(),
                    }),
                },
            )
            await db.commit()
        except Exception:
            pass
        raise invalid_exc

    # ── SUCCESS: Mark activated, clear code (one-time use) ───────────────────
    activated_at = datetime.now(timezone.utc)
    try:
        await db.execute(
            text("""
                UPDATE employees
                SET activation_status          = 'activated',
                    activated_at               = :activated_at,
                    activation_code_hash       = NULL,
                    activation_code_expires_at = NULL,
                    updated_at                 = NOW()
                WHERE id::text = :id AND deleted_at IS NULL
            """),
            {"activated_at": activated_at, "id": body.employee_id},
        )

        # Audit log: activation_success
        await db.execute(
            text("""
                INSERT INTO audit_logs (action, resource_type, resource_id, changes)
                VALUES ('activation_success', 'kbt_activation', :emp_id, CAST(:changes AS JSONB))
            """),
            {
                "emp_id": body.employee_id,
                "changes": json.dumps({
                    "employee_id":  body.employee_id,
                    "employee_name": row.name,
                    "department":   row.department,
                    "device_info":  body.device_info,
                    "activated_at": activated_at.isoformat(),
                }),
            },
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"[kbt-activate] DB update failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Activation failed due to a server error.")

    # Issue session JWT
    token = _make_employee_token(row.id, row.email, row.name)
    logger.info(f"[kbt-activate] activation SUCCESS: {body.employee_id}")

    # Fire-and-forget: notify manager + HR
    await _notify_staff_of_activation(
        db=db,
        employee_name=row.name,
        department=row.department or "Unknown",
        activation_ts=activated_at.strftime("%Y-%m-%d %H:%M UTC"),
    )

    return ActivateResponse(
        ok=True,
        message="KBT activated successfully. System initialised.",
        access_token=token,
    )


# ── POST /kbt/auto-login ───────────────────────────────────────────────────────

@router.post("/auto-login", response_model=AutoLoginResponse)
async def kbt_auto_login(
    body: AutoLoginRequest,
    db:   AsyncSession = Depends(get_db),
):
    """
    Zero-login authentication for the KBT Executable (subsequent runs).

    The binary embeds:  { employee_id, token }
    Backend stores:     SHA-256(token)  in employees.kbt_token_hash

    Steps:
    1. Look up employee by employee_id
    2. Check activation_status == 'activated' (blocks unactivated clients)
    3. Hash the incoming token and compare to stored hash
    4. On match → issue a 12-hour session JWT (same format as manual login)
    5. On mismatch / missing hash → 401
    """
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized or expired KBT client. Contact your IT administrator.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Fetch employee + token hash in one query
    result = await db.execute(
        text("""
            SELECT e.id::text AS id,
                   e.email,
                   e.name,
                   e.department,
                   e.role,
                   e.status,
                   e.activation_status,
                   e.kbt_token_hash,
                   e.kbt_token_expires_at
            FROM   employees e
            WHERE  e.id::text = :emp_id
              AND  e.deleted_at IS NULL
        """),
        {"emp_id": body.employee_id},
    )
    row = result.fetchone()

    if not row:
        logger.warning(f"[kbt-auth] employee_id not found: {body.employee_id}")
        raise invalid_exc

    if row.status != "active":
        logger.warning(f"[kbt-auth] employee inactive: {body.employee_id}")
        raise invalid_exc

    # ── NEW: Activation guard ─────────────────────────────────────────────────
    if row.activation_status != "activated":
        logger.warning(f"[kbt-auth] KBT not activated for: {body.employee_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "KBT not activated. Please run the executable and enter your activation code "
                "when prompted. Check your onboarding email for the code."
            ),
        )

    if not row.kbt_token_hash:
        logger.warning(f"[kbt-auth] no KBT token provisioned for: {body.employee_id}")
        raise invalid_exc

    # Check optional expiry
    if row.kbt_token_expires_at:
        exp = row.kbt_token_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            logger.warning(f"[kbt-auth] token expired for: {body.employee_id}")
            raise invalid_exc

    # Constant-time hash comparison
    incoming_hash = _hash_token(body.token)
    if not secrets.compare_digest(incoming_hash, row.kbt_token_hash):
        logger.warning(f"[kbt-auth] token mismatch for: {body.employee_id}")
        raise invalid_exc

    # Issue session JWT
    token = _make_employee_token(row.id, row.email, row.name)

    # Update last-seen (best effort)
    try:
        await db.execute(
            text("UPDATE employees SET updated_at = NOW() WHERE id::text = :id"),
            {"id": row.id},
        )
        await db.commit()
    except Exception:
        pass

    logger.info(f"[kbt-auth] auto-login success: {body.employee_id}")
    return AutoLoginResponse(
        access_token=token,
        employee_id=row.id,
        name=row.name,
        email=row.email,
        department=row.department,
        role=row.role,
    )


# ── GET /kbt/token/{employee_id} ──────────────────────────────────────────────

@router.get("/token/{employee_id}", response_model=KBTTokenResponse)
async def generate_kbt_token(
    employee_id: str,
    caller: dict = Depends(require_role(["admin", "manager", "hr"])),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate (or rotate) a KBT token for an employee.
    The raw token is returned ONCE and stored nowhere on the server.
    Only admin / manager / HR can call this endpoint.
    """
    # Validate employee exists
    result = await db.execute(
        text("SELECT id::text AS id, name, email FROM employees WHERE id::text = :id AND deleted_at IS NULL"),
        {"id": employee_id},
    )
    emp = result.fetchone()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found.")

    raw_token  = _generate_raw_token()
    token_hash = _hash_token(raw_token)
    now        = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=_TOKEN_TTL_DAYS)

    await db.execute(
        text("""
            UPDATE employees
            SET kbt_token_hash        = :hash,
                kbt_token_expires_at  = :expires,
                kbt_generated_at      = :now,
                updated_at            = NOW()
            WHERE id::text = :id
        """),
        {
            "hash":    token_hash,
            "expires": expires_at,
            "now":     now,
            "id":      employee_id,
        },
    )
    await db.commit()

    logger.info(f"[kbt-auth] token generated for employee: {employee_id} by {caller.get('id', '?')}")

    api_url = str(settings.API_URL) if hasattr(settings, "API_URL") else ""

    return KBTTokenResponse(
        employee_id=employee_id,
        token=raw_token,
        api_url=api_url,
        generated_at=now.isoformat(),
        expires_at=expires_at.isoformat(),
    )


# ── DELETE /kbt/revoke/{employee_id} ─────────────────────────────────────────

@router.delete("/revoke/{employee_id}", status_code=200)
async def revoke_kbt_token(
    employee_id: str,
    caller: dict = Depends(require_role(["admin", "manager", "hr"])),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke a KBT token. The employee's executable will no longer be able
    to authenticate. Use for offboarding or security incidents.
    Also resets activation_status → 'pending_activation' so a new token
    + activation code must be generated before re-use.
    """
    result = await db.execute(
        text("""
            UPDATE employees
            SET kbt_token_hash              = NULL,
                kbt_token_expires_at        = NULL,
                activation_status           = 'pending_activation',
                activation_code_hash        = NULL,
                activation_code_expires_at  = NULL,
                activated_at                = NULL,
                updated_at                  = NOW()
            WHERE id::text = :id AND deleted_at IS NULL
        """),
        {"id": employee_id},
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Employee not found.")

    logger.info(f"[kbt-auth] token REVOKED for: {employee_id} by {caller.get('id', '?')}")
    return {"ok": True, "message": "KBT token revoked. The employee's executable is now blocked."}
