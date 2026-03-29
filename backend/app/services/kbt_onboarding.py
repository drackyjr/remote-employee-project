"""
TBAPS — KBT Onboarding Service
================================
Automates the full onboarding pipeline triggered on employee creation:

  1. Generate unique KBT token for the employee
  2. Generate one-time activation code (6 char alphanumeric)
  3. Build kbt_identity.json bundle
  4. Generate a signed, time-limited download URL for the KBT binary
  5. Send onboarding email with formal instructions + download link + activation code
  6. Write audit log entry

Called by any endpoint that creates an employee:
  - POST /admin/create-employee
  - POST /admin/create-user  (role == "employee")

Usage:
    from app.services.kbt_onboarding import run_kbt_onboarding
    await run_kbt_onboarding(db, employee_id, employee_name, employee_email, created_by_id)
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.email_service import send_kbt_onboarding_email

logger = logging.getLogger(__name__)

# ── Token settings ─────────────────────────────────────────────────────────────
_TOKEN_BYTES               = 48        # 384-bit raw token
_TOKEN_TTL_DAYS            = 365       # KBT token validity
_LINK_TTL_HOURS            = 72        # Signed download link validity
_ACTIVATION_CODE_TTL_DAYS  = 30        # Activation code validity window


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_raw_token() -> str:
    """Cryptographically secure 48-byte URL-safe token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _generate_activation_code() -> str:
    """
    Generate a 6-character uppercase alphanumeric activation code.
    Example: "A3F7K2"

    Uses secrets.token_hex to get random bytes, then converts to uppercase hex.
    3 random bytes → 6 hex chars → all uppercase.
    """
    return secrets.token_hex(3).upper()


def _build_signed_download_url(employee_id: str, employee_name: str) -> str:
    """
    Build a signed, time-limited download URL for the KBT identity bundle.

    URL: GET /api/v1/kbt/download/{employee_id}?expires=<ts>&sig=<hmac>

    The HMAC-SHA256 signature uses settings.JWT_SECRET_KEY as the signing key,
    binding the URL to (employee_id + expiry timestamp).
    The download endpoint verifies the signature before serving the file.

    Returns the full URL string.
    """
    expiry = int(time.time()) + _LINK_TTL_HOURS * 3600
    message = f"{employee_id}:{expiry}"
    sig = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    base = settings.SERVER_URL.rstrip("/")
    name_slug = quote(employee_name.replace(" ", "_"), safe="")
    return (
        f"{base}/api/v1/kbt/download/{employee_id}"
        f"?expires={expiry}&sig={sig}&name={name_slug}"
    )


def build_identity_bundle(
    employee_id: str,
    raw_token: str,
    api_url: str,
) -> dict:
    """Build the kbt_identity.json payload."""
    now = datetime.now(timezone.utc)
    return {
        "employee_id":  employee_id,
        "token":        raw_token,
        "api_url":      api_url.rstrip("/"),
        "generated_at": now.isoformat(),
        "expires_at":   (now + timedelta(days=_TOKEN_TTL_DAYS)).isoformat(),
    }


# ── Audit log ─────────────────────────────────────────────────────────────────

async def _write_audit(
    db: AsyncSession,
    employee_id: str,
    created_by_id: str,
    kbt_generated: bool,
    email_sent: bool,
    activation_code_generated: bool = False,
    error: Optional[str] = None,
    action: str = "CREATE",
):
    try:
        await db.execute(
            text("""
                INSERT INTO audit_logs (action, resource_type, resource_id, changes)
                VALUES (:action, 'kbt_onboarding', :emp_id, CAST(:changes AS JSONB))
            """),
            {
                "action": action,
                "emp_id": employee_id,
                "changes": json.dumps({
                    "created_by":               created_by_id,
                    "action":                   "employee_onboarding_kbt_sent",
                    "kbt_generated":            kbt_generated,
                    "email_sent":               email_sent,
                    "activation_code_generated": activation_code_generated,
                    "error":                    error,
                    "timestamp":                datetime.now(timezone.utc).isoformat(),
                }),
            },
        )
        await db.commit()
    except Exception as e:
        logger.warning(f"[kbt-onboarding] Audit log write failed: {e}")


# ── Main pipeline ──────────────────────────────────────────────────────────────

async def run_kbt_onboarding(
    db: AsyncSession,
    employee_id: str,
    employee_name: str,
    employee_email: str,
    created_by_id: str = "system",
) -> dict:
    """
    Full KBT onboarding pipeline for a newly created employee.

    Steps:
      1. Generate raw token + hash
      2. Generate one-time activation code + hash
      3. Store both hashes in employees table (status = pending_activation)
      4. Build signed download URL
      5. Send KBT onboarding email (includes activation code)
      6. Write audit log

    Returns:
      {
        "ok": True,
        "kbt_generated": True,
        "email_sent": True,
        "download_url": "...",
      }

    Never raises — errors are caught, logged, and returned in the result dict.
    """
    result = {
        "ok":                       False,
        "kbt_generated":            False,
        "activation_code_generated": False,
        "email_sent":               False,
        "download_url":             None,
        "error":                    None,
    }

    # ── Step 1: Generate KBT token ────────────────────────────────────────────
    try:
        raw_token        = _generate_raw_token()
        token_hash       = _hash_token(raw_token)
        activation_code  = _generate_activation_code()
        act_code_hash    = _hash_token(activation_code)
        now              = datetime.now(timezone.utc)
        token_expires_at = now + timedelta(days=_TOKEN_TTL_DAYS)
        act_code_expires = now + timedelta(days=_ACTIVATION_CODE_TTL_DAYS)

        await db.execute(
            text("""
                UPDATE employees
                SET kbt_token_hash              = :token_hash,
                    kbt_token_expires_at        = :token_expires,
                    kbt_generated_at            = :now,
                    activation_code_hash        = :act_hash,
                    activation_code_expires_at  = :act_expires,
                    activation_status           = 'pending_activation',
                    updated_at                  = NOW()
                WHERE id::text = :id AND deleted_at IS NULL
            """),
            {
                "token_hash":    token_hash,
                "token_expires": token_expires_at,
                "now":           now,
                "act_hash":      act_code_hash,
                "act_expires":   act_code_expires,
                "id":            employee_id,
            },
        )
        await db.commit()
        result["kbt_generated"]            = True
        result["activation_code_generated"] = True
        logger.info(f"[kbt-onboarding] Token + activation code stored for: {employee_id}")
    except Exception as e:
        result["error"] = f"Token generation failed: {e}"
        logger.error(f"[kbt-onboarding] Token generation error: {e}", exc_info=True)
        await _write_audit(db, employee_id, created_by_id,
                           kbt_generated=False, email_sent=False, error=str(e))
        return result

    # ── Step 2: Build download URL ────────────────────────────────────────────
    try:
        download_url = _build_signed_download_url(employee_id, employee_name)
        result["download_url"] = download_url
        logger.info(f"[kbt-onboarding] Download URL built for: {employee_id}")
    except Exception as e:
        result["error"] = f"Download URL build failed: {e}"
        logger.error(f"[kbt-onboarding] Download URL error: {e}")
        await _write_audit(db, employee_id, created_by_id,
                           kbt_generated=True, email_sent=False,
                           activation_code_generated=True, error=str(e))
        return result

    # ── Step 3: Send onboarding email (with activation code) ──────────────────
    try:
        await send_kbt_onboarding_email(
            to_email=employee_email,
            to_name=employee_name,
            employee_id=employee_id,
            download_url=download_url,
            activation_code=activation_code,
        )
        result["email_sent"] = True
        logger.info(f"[kbt-onboarding] Email sent to: {employee_email}")
    except Exception as e:
        result["error"] = f"Email send failed: {e}"
        logger.error(f"[kbt-onboarding] Email error: {e}", exc_info=True)
        # Don't abort — KBT was generated successfully; admin can resend
        await _write_audit(db, employee_id, created_by_id,
                           kbt_generated=True, email_sent=False,
                           activation_code_generated=True, error=str(e))
        result["ok"] = True   # Partial success
        return result

    # ── Step 4: Audit ─────────────────────────────────────────────────────────
    await _write_audit(db, employee_id, created_by_id,
                       kbt_generated=True, email_sent=True,
                       activation_code_generated=True)
    result["ok"] = True
    return result


# ── URL verification helper (used by download endpoint) ───────────────────────

def verify_signed_download_url(employee_id: str, expires: str, sig: str) -> bool:
    """
    Verify the HMAC signature of a signed download URL.
    Returns True if valid and not expired.
    """
    try:
        exp_ts = int(expires)
    except (ValueError, TypeError):
        return False

    if time.time() > exp_ts:
        logger.warning(f"[kbt-download] Link expired for: {employee_id}")
        return False

    message = f"{employee_id}:{exp_ts}"
    expected_sig = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return secrets.compare_digest(expected_sig, sig)
