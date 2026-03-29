"""
Admin — System User & Department Management
All endpoints require ADMIN role.

GET  /admin/users              — list all system users
POST /admin/users              — create manager or HR user
PATCH /admin/users/{id}        — update role / department / active
DELETE /admin/users/{id}       — deactivate (soft delete)
GET  /admin/departments        — list departments
POST /admin/departments        — create department
DELETE /admin/departments/{id} — delete empty department
"""

from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_admin, hash_password
from app.api.v1.admin_send_agent import _build_download_urls
from app.services.email_service import send_notification_email, send_agent_email
from app.services.kbt_onboarding import run_kbt_onboarding

import uuid

router = APIRouter(prefix="/admin", tags=["admin-users"])


# ── Request / Response models ──────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    username:      str
    email:         EmailStr
    password:      str
    role:          str            # "admin" | "manager" | "hr"
    department_id: Optional[str] = None

class CreateManagerRequest(BaseModel):
    name:       str
    email:      EmailStr
    password:   str
    department: str
    role: str = "manager"

class CreateEmployeeRequest(BaseModel):
    name:       str
    email:      EmailStr
    department: Optional[str] = None

class UpdateUserRequest(BaseModel):
    role:          Optional[str] = None
    department_id: Optional[str] = None
    is_active:     Optional[bool] = None
    email:         Optional[str] = None


class CreateDepartmentRequest(BaseModel):
    name: str


class CreateUnifiedUserRequest(BaseModel):
    name:       str
    email:      EmailStr
    role:       str                      # "employee" | "manager" | "hr"
    department: str
    password:   Optional[str] = None     # required for manager/hr, ignored for employee
    send_email: bool = True
    email_type: Optional[str] = None     # "employee_setup" | "staff_notification" | None=auto


# ── Department Endpoints ───────────────────────────────────────────────────────

@router.get("/departments")
async def list_departments(
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """List all departments."""
    result = await db.execute(
        text("SELECT id::text AS id, name, created_at FROM departments ORDER BY name")
    )
    return [dict(r._mapping) for r in result.fetchall()]


@router.post("/departments", status_code=status.HTTP_201_CREATED)
async def create_department(
    body: CreateDepartmentRequest,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Create a new department (admin only)."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Department name cannot be empty")

    try:
        result = await db.execute(
            text("INSERT INTO departments (id, name) VALUES (:id, :name) RETURNING id::text AS id, name"),
            {"id": str(uuid.uuid4()), "name": name},
        )
        await db.commit()
        row = result.fetchone()
        return {"id": row.id, "name": row.name}
    except Exception as e:
        await db.rollback()
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail=f"Department '{name}' already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/departments/{dept_id}")
async def delete_department(
    dept_id: str,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Delete a department only if no system users are assigned to it."""
    # Check for assigned users
    check = await db.execute(
        text("SELECT COUNT(*) FROM system_users WHERE department_id::text = :id AND is_active = TRUE"),
        {"id": dept_id},
    )
    count = check.scalar()
    if count:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete: {count} active user(s) assigned to this department.",
        )
    await db.execute(
        text("DELETE FROM departments WHERE id::text = :id"),
        {"id": dept_id},
    )
    await db.commit()
    return {"ok": True, "message": "Department deleted"}


# ── System User Endpoints ──────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """List all system users with role + department."""
    result = await db.execute(
        text("""
            SELECT su.id::text AS id, su.username, su.email, su.role,
                   su.is_active, su.last_login,
                   su.department_id::text AS department_id,
                   d.name AS department_name,
                   su.created_at
            FROM system_users su
            LEFT JOIN departments d ON su.department_id = d.id
            ORDER BY su.role, su.username
        """)
    )
    return [dict(r._mapping) for r in result.fetchall()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new system user (admin, manager, or HR).

    Rules:
    - Managers MUST have a department_id.
    - Admins and HR may have department_id = null.
    - Password must be >= 8 characters.
    """
    # Validate role
    valid_roles = {"admin", "manager", "hr"}
    if body.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {valid_roles}")

    if body.role == "manager" and not body.department_id:
        raise HTTPException(status_code=400, detail="Managers must be assigned a department_id")

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    hashed = hash_password(body.password)
    new_id = str(uuid.uuid4())

    try:
        result = await db.execute(
            text("""
                INSERT INTO system_users
                    (id, username, email, password_hash, role, department_id, is_active, created_at, updated_at)
                VALUES
                    (:id, :username, :email, :hash, :role, :dept, TRUE, NOW(), NOW())
                RETURNING id::text AS id, username, email, role,
                          department_id::text AS department_id
            """),
            {
                "id":       new_id,
                "username": body.username,
                "email":    body.email,
                "hash":     hashed,
                "role":     body.role,
                "dept":     body.department_id,
            },
        )
        await db.commit()
        row = result.fetchone()
        return {
            "id":              row.id,
            "username":        row.username,
            "email":           row.email,
            "role":            row.role,
            "department_id":   row.department_id,
        }
    except Exception as e:
        await db.rollback()
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Username or email already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create-manager", status_code=status.HTTP_201_CREATED)
async def create_manager(
    body: CreateManagerRequest,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """
    Convenience endpoint for the Admin Dashboard "Manager Management" feature.
    Creates a Manager or HR user, finds their department ID, and logs the action.
    """
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Validate role
    valid_roles = {"manager", "hr"}
    if body.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {valid_roles}")

    # Resolve department name to ID
    dept_res = await db.execute(
        text("SELECT id FROM departments WHERE name ILIKE :name"),
        {"name": f"%{body.department}%"}
    )
    dept_id = dept_res.scalar()
    if not dept_id:
        raise HTTPException(status_code=400, detail=f"Department '{body.department}' not found")

    hashed = hash_password(body.password)
    new_id = str(uuid.uuid4())

    try:
        # Create user
        await db.execute(
            text("""
                INSERT INTO system_users
                    (id, username, email, password_hash, role, department_id, is_active, created_at, updated_at)
                VALUES
                    (:id, :username, :email, :hash, :role, :dept, TRUE, NOW(), NOW())
            """),
            {
                "id":       new_id,
                "username": body.name,
                "email":    body.email,
                "hash":     hashed,
                "role":     body.role,
                "dept":     dept_id,
            },
        )
        
        # Create audit log
        import json
        await db.execute(
            text("""
                INSERT INTO audit_logs
                    (action, resource_type, resource_id, changes)
                VALUES
                    ('CREATE', 'system_user', :new_id, CAST(:changes AS JSONB))
            """),
            {
                "new_id":  new_id,
                "changes": json.dumps({"admin_id": str(admin["id"]), "event": "create_manager"})
            }
        )
        await db.commit()

        # Fire and forget notification email
        import asyncio
        asyncio.create_task(
            send_notification_email(
                to_email=body.email,
                to_name=body.name,
                role=body.role,
                department=body.department,
                username=body.email,  # Or body.name, but email is safer for login
                temp_password=body.password,
            )
        )

        return {"id": new_id, "message": f"{body.role.capitalize()} account created successfully"}
    except Exception as e:
        await db.rollback()
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Email or username already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create-employee", status_code=status.HTTP_201_CREATED)
async def create_employee(
    body: CreateEmployeeRequest,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates an Employee account (which is stored in the `employees` table natively),
    and instantly sends them the full onboarding email with agent download links.
    """
    new_id = str(uuid.uuid4())

    try:
        # Check if a soft-deleted employee with same email exists — reactivate if so
        existing = await db.execute(
            text("SELECT id::text FROM employees WHERE email = :email AND deleted_at IS NOT NULL"),
            {"email": body.email}
        )
        existing_row = existing.fetchone()

        if existing_row:
            # Reactivate the soft-deleted record
            new_id = existing_row[0]
            await db.execute(
                text("""
                    UPDATE employees
                    SET name       = :name,
                        department = :dept,
                        status     = 'active',
                        deleted_at = NULL,
                        updated_at = NOW()
                    WHERE id = CAST(:id AS UUID)
                """),
                {"name": body.name, "dept": body.department, "id": new_id}
            )
        else:
            # Fresh insert
            await db.execute(
                text("""
                    INSERT INTO employees
                        (id, name, email, department, status, created_at, updated_at)
                    VALUES
                        (:id, :name, :email, :dept, 'active', NOW(), NOW())
                """),
                {
                    "id":    new_id,
                    "name":  body.name,
                    "email": body.email,
                    "dept":  body.department,
                },
            )
        
        # Create audit log
        import json
        await db.execute(
            text("""
                INSERT INTO audit_logs
                    (action, resource_type, resource_id, changes)
                VALUES
                    ('CREATE', 'employee', :new_id, CAST(:changes AS JSONB))
            """),
            {
                "new_id":  new_id,
                "changes": json.dumps({"admin_id": str(admin["id"]), "event": "create_employee"})
            }
        )
        await db.commit()

        # --- KBT Onboarding Pipeline ---
        # Fire-and-forget: generate token, send KBT email with signed download URL.
        # Falls back gracefully — employee is created even if email fails.
        import asyncio
        asyncio.create_task(
            run_kbt_onboarding(
                db=db,
                employee_id=new_id,
                employee_name=body.name,
                employee_email=body.email,
                created_by_id=str(admin["id"]),
            )
        )

        return {"id": new_id, "message": "Employee account created and KBT onboarding email queued"}
    except Exception as e:
        await db.rollback()
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Email already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing system user's role, department, or active status."""
    updates = {}
    if body.role is not None:
        if body.role not in {"admin", "manager", "hr"}:
            raise HTTPException(status_code=400, detail="Invalid role")
        updates["role"] = body.role
    if body.department_id is not None:
        updates["department_id"] = body.department_id
    if body.is_active is not None:
        updates["is_active"] = body.is_active
    if body.email is not None:
        updates["email"] = body.email

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["updated_at"] = datetime.now(timezone.utc)
    updates["user_id"] = user_id

    await db.execute(
        text(f"UPDATE system_users SET {set_clause}, updated_at = :updated_at WHERE id::text = :user_id"),
        updates,
    )
    await db.commit()
    return {"ok": True, "message": "User updated"}


@router.delete("/users/{user_id}")
async def deactivate_user(
    user_id: str,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate (soft-delete) a system user. Prevents login but preserves audit trail."""
    # Prevent self-deactivation
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")

    await db.execute(
        text("UPDATE system_users SET is_active = FALSE, updated_at = NOW() WHERE id::text = :id"),
        {"id": user_id},
    )
    await db.commit()
    return {"ok": True, "message": "User deactivated"}


@router.get("/employees")
async def list_employees(
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """List all employees."""
    result = await db.execute(
        text("""
            SELECT id::text AS id, name, email, department, status, created_at
            FROM employees 
            WHERE deleted_at IS NULL AND status != 'offboarded'
            ORDER BY name
        """)
    )
    return [dict(r._mapping) for r in result.fetchall()]


@router.delete("/employees/{user_id}")
async def remove_employee(
    user_id: str,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete an employee using a direct status update."""
    result = await db.execute(
        text("""
            UPDATE employees
            SET deleted_at = NOW(),
                status     = 'offboarded'
            WHERE id = CAST(:id AS UUID)
            AND deleted_at IS NULL
        """),
        {"id": user_id}
    )
    if result.rowcount == 0:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Employee not found or already deleted")

    # Create audit log
    import json
    await db.execute(
        text("""
            INSERT INTO audit_logs
                (action, resource_type, resource_id, changes)
            VALUES
                ('DELETE', 'employee', :new_id, CAST(:changes AS JSONB))
        """),
        {
            "new_id":  user_id,
            "changes": json.dumps({"admin_id": str(admin["id"]), "event": "delete_employee"})
        }
    )
    await db.commit()

    return {"ok": True, "message": "Employee successfully deleted"}


# ── Unified People Management ──────────────────────────────────────────────────

@router.get("/people")
async def list_all_people(
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Return combined list of all system_users (managers/HR) and active employees."""
    staff_result = await db.execute(text("""
        SELECT su.id::text AS id,
               su.username  AS name,
               su.email,
               su.role,
               su.is_active AS status_active,
               d.name       AS department,
               su.created_at
        FROM system_users su
        LEFT JOIN departments d ON su.department_id = d.id
        WHERE su.role IN ('manager','hr') AND su.is_active = TRUE
        ORDER BY su.role, su.username
    """))
    emp_result = await db.execute(text("""
        SELECT id::text AS id,
               name,
               email,
               'employee'  AS role,
               (status = 'active') AS status_active,
               department,
               created_at
        FROM employees
        WHERE deleted_at IS NULL AND status != 'offboarded'
        ORDER BY name
    """))
    people = [dict(r._mapping) for r in staff_result.fetchall()] + \
             [dict(r._mapping) for r in emp_result.fetchall()]
    return people


@router.post("/create-user", status_code=status.HTTP_201_CREATED)
async def create_unified_user(
    body: CreateUnifiedUserRequest,
    admin: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """
    Unified user creation endpoint for all roles.
    Routes the INSERT to the correct table and fires the correct email.
    """
    import json, asyncio

    valid_roles = {"employee", "manager", "hr"}
    if body.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {valid_roles}")

    # Determine effective email type
    email_type = body.email_type
    if not email_type:
        email_type = "employee_setup" if body.role == "employee" else "staff_notification"

    # Validate email_type vs role (prevent employees getting staff email and vice-versa)
    if body.role == "employee" and email_type == "staff_notification":
        raise HTTPException(status_code=400, detail="Employees cannot receive staff_notification email")

    new_id = str(uuid.uuid4())

    try:
        if body.role == "employee":
            # --- INSERT into employees table ---
            existing = await db.execute(
                text("SELECT id::text FROM employees WHERE email = :email AND deleted_at IS NOT NULL"),
                {"email": body.email}
            )
            existing_row = existing.fetchone()
            if existing_row:
                new_id = existing_row[0]
                await db.execute(text("""
                    UPDATE employees
                    SET name=:name, department=:dept, status='active', deleted_at=NULL, updated_at=NOW()
                    WHERE id = CAST(:id AS UUID)
                """), {"name": body.name, "dept": body.department, "id": new_id})
            else:
                # Check uniqueness across employees
                dup = await db.execute(text("SELECT 1 FROM employees WHERE email=:email AND deleted_at IS NULL"), {"email": body.email})
                if dup.fetchone():
                    raise HTTPException(status_code=409, detail="Email already exists")
                await db.execute(text("""
                    INSERT INTO employees (id, name, email, department, status, created_at, updated_at)
                    VALUES (:id, :name, :email, :dept, 'active', NOW(), NOW())
                """), {"id": new_id, "name": body.name, "email": body.email, "dept": body.department})

        else:
            # --- INSERT into system_users table (manager/HR) ---
            if not body.password or len(body.password) < 8:
                raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
            hashed = hash_password(body.password)
            dept_res = await db.execute(
                text("SELECT id FROM departments WHERE name ILIKE :name"),
                {"name": f"%{body.department}%"}
            )
            dept_row = dept_res.fetchone()
            if not dept_row:
                raise HTTPException(status_code=404, detail=f"Department '{body.department}' not found")
            dept_id = dept_row[0]

            # Check if a previously deactivated user with the same email exists → reactivate
            existing_staff = await db.execute(
                text("SELECT id::text FROM system_users WHERE email = :email AND is_active = FALSE"),
                {"email": body.email}
            )
            existing_staff_row = existing_staff.fetchone()

            if existing_staff_row:
                new_id = existing_staff_row[0]
                await db.execute(text("""
                    UPDATE system_users
                    SET username=:name, password_hash=:hash, role=:role,
                        department_id=:dept, is_active=TRUE, updated_at=NOW()
                    WHERE id = CAST(:id AS UUID)
                """), {"name": body.name, "hash": hashed, "role": body.role, "dept": dept_id, "id": new_id})
            else:
                await db.execute(text("""
                    INSERT INTO system_users (id, username, email, password_hash, role, department_id, is_active, created_at, updated_at)
                    VALUES (:id, :name, :email, :hash, :role, :dept, TRUE, NOW(), NOW())
                """), {"id": new_id, "name": body.name, "email": body.email, "hash": hashed, "role": body.role, "dept": dept_id})

        # Audit log
        await db.execute(text("""
            INSERT INTO audit_logs (action, resource_type, resource_id, changes)
            VALUES ('CREATE', :rtype, :rid, CAST(:changes AS JSONB))
        """), {
            "rtype": body.role,
            "rid": new_id,
            "changes": json.dumps({"admin_id": str(admin["id"]), "email_type": email_type})
        })
        await db.commit()

        # Fire email
        if body.send_email:
            if email_type == "employee_setup":
                # --- KBT Onboarding Pipeline ---
                asyncio.create_task(
                    run_kbt_onboarding(
                        db=db,
                        employee_id=new_id,
                        employee_name=body.name,
                        employee_email=body.email,
                        created_by_id=str(admin["id"]),
                    )
                )
            else:
                asyncio.create_task(send_notification_email(
                    to_email=body.email, to_name=body.name,
                    role=body.role, department=body.department,
                    username=body.email, temp_password=body.password,
                ))

        msg = "User created successfully"
        if body.send_email:
            msg += f" and {email_type.replace('_', ' ')} email queued"
        return {"id": new_id, "role": body.role, "message": msg}

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        if "unique" in str(e).lower() or "already exists" in str(e).lower():
            raise HTTPException(status_code=409, detail="Email already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/seed-admin")
async def seed_first_admin(db: AsyncSession = Depends(get_db)):
    """
    Bootstrap: create first admin if system_users is empty.
    Disable this endpoint in production after first use.
    POST body: { username, email, password }
    """
    # Only allow if no admin exists
    check = await db.execute(
        text("SELECT COUNT(*) FROM system_users WHERE role = 'admin'")
    )
    count = check.scalar()
    if count:
        raise HTTPException(
            status_code=409,
            detail="At least one admin already exists. Use admin panel to manage users.",
        )

    from pydantic import BaseModel as PM
    # Seed with a default first admin
    hashed = hash_password("Admin@1234")
    new_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO system_users (id, username, email, password_hash, role, is_active)
            VALUES (:id, 'admin', 'admin@tbaps.local', :hash, 'admin', TRUE)
        """),
        {"id": new_id, "hash": hashed},
    )
    await db.commit()
    return {
        "ok": True,
        "message": "First admin created: username=admin, password=Admin@1234. Change immediately.",
    }
