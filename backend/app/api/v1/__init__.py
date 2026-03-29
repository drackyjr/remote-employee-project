"""
TBAPS API v1 — Router Assembly
"""

import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)

api_router = APIRouter()

# ── Admin auth — always required ───────────────────────────────────────────────
from app.api.v1.auth import router as auth_router
api_router.include_router(auth_router, tags=["admin-auth"])

# ── Agent monitoring (NEF agent) — always required ────────────────────────────
from app.api.v1.agent import router as agent_router
api_router.include_router(agent_router, tags=["agent"])

# ── Agent JWT token endpoint (NEF agent auth) ─────────────────────────────────
from app.api.v1.agent_token import router as agent_token_router
api_router.include_router(agent_token_router, tags=["agent-auth"])

# ── Trust Dashboard endpoint ───────────────────────────────────────────────────
try:
    from app.api.v1.trust_dashboard import router as trust_dashboard_router
    api_router.include_router(trust_dashboard_router)
except Exception as e:
    logger.warning(f"trust_dashboard router skipped: {e}")

# ── Optional routers — graceful degradation if import fails ───────────────────

try:
    from app.api.v1.traffic import router as traffic_router
    api_router.include_router(traffic_router, tags=["traffic"])
except Exception as e:
    logger.warning(f"traffic router skipped: {e}")

try:
    from app.api.v1.copilot import router as copilot_router
    api_router.include_router(copilot_router, tags=["copilot"])
except Exception as e:
    logger.warning(f"copilot router skipped: {e}")

try:
    from app.api.v1.nef_certificates import router as nef_router
    api_router.include_router(nef_router, tags=["nef"])
except Exception as e:
    logger.warning(f"nef_certificates router skipped: {e}")

try:
    from app.api.v1.admin_send_agent import router as send_agent_router
    api_router.include_router(send_agent_router, tags=["admin-send-agent"])
except Exception as e:
    logger.warning(f"admin_send_agent router skipped: {e}")

# ── RBAC routers ───────────────────────────────────────────────────────────────

try:
    from app.api.v1.rbac_auth import router as rbac_auth_router
    api_router.include_router(rbac_auth_router, tags=["rbac-auth"])
except Exception as e:
    logger.warning(f"rbac_auth router skipped: {e}")

try:
    from app.api.v1.users_admin import router as users_admin_router
    api_router.include_router(users_admin_router, tags=["admin-users"])
except Exception as e:
    logger.warning(f"users_admin router skipped: {e}")

try:
    from app.api.v1.manager_dashboard import router as manager_router
    api_router.include_router(manager_router, tags=["manager-dashboard"])
except Exception as e:
    logger.warning(f"manager_dashboard router skipped: {e}")

try:
    from app.api.v1.hr_dashboard import router as hr_router
    api_router.include_router(hr_router, tags=["hr-dashboard"])
except Exception as e:
    logger.warning(f"hr_dashboard router skipped: {e}")

try:
    from app.api.v1.users import router as users_router
    api_router.include_router(users_router, tags=["users"])
except Exception as e:
    logger.warning(f"users router skipped: {e}")

# ── Employee-facing routers (NEF client app) ───────────────────────────────────

try:
    from app.api.v1.employee_auth import router as employee_auth_router
    api_router.include_router(employee_auth_router, tags=["employee-auth"])
except Exception as e:
    logger.warning(f"employee_auth router skipped: {e}")

try:
    from app.api.v1.chat import router as chat_router
    api_router.include_router(chat_router, tags=["chat"])
except Exception as e:
    logger.warning(f"chat router skipped: {e}")

try:
    from app.api.v1.work_sessions import router as work_sessions_router
    api_router.include_router(work_sessions_router, tags=["work-sessions"])
except Exception as e:
    logger.warning(f"work_sessions router skipped: {e}")

# ── KBT Executable auto-login & token management ──────────────────────────────
try:
    from app.api.v1.kbt_auth import router as kbt_auth_router
    api_router.include_router(kbt_auth_router, tags=["kbt-auth"])
except Exception as e:
    logger.warning(f"kbt_auth router skipped: {e}")

# ── KBT Download (signed URL) & resend onboarding email ───────────────────────
try:
    from app.api.v1.kbt_download import router as kbt_download_router
    api_router.include_router(kbt_download_router, tags=["kbt-download"])
except Exception as e:
    logger.warning(f"kbt_download router skipped: {e}")
