#!/usr/bin/env python3
"""
generate_kbt.py — Per-Employee KBT Executable Packager
-------------------------------------------------------
Admin CLI tool that:
1. Generates a KBT token for an employee via the backend API
2. Writes kbt_identity.json
3. Copies the base binary and attaches identity alongside it
4. (Optionally) emails the download link to the employee

Usage:
  python scripts/generate_kbt.py \\
    --employee-id EMP_ID \\
    --api-url https://tbaps.company.com \\
    --admin-token ADMIN_JWT \\
    --base-binary agent/dist/KBT \\
    --output dist/employees/KBT_john_doe

On Windows, use KBT.exe as the base-binary.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


def _get_kbt_token(api_url: str, employee_id: str, admin_token: str) -> dict:
    """Call GET /api/v1/kbt/token/{employee_id} to generate a signed token."""
    url = f"{api_url.rstrip('/')}/api/v1/kbt/token/{employee_id}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    if resp.status_code == 404:
        print(f"❌ Employee ID not found: {employee_id}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code != 200:
        print(f"❌ Backend error {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


def build_identity(token_data: dict, api_url: str) -> dict:
    """Build the kbt_identity.json bundle."""
    return {
        "employee_id":  token_data["employee_id"],
        "token":        token_data["token"],
        "api_url":      api_url.rstrip("/"),
        "generated_at": token_data["generated_at"],
        "expires_at":   token_data["expires_at"],
    }


def package_binary(base_binary: Path, identity: dict, output: Path):
    """
    Create a personalised KBT binary alongside its identity file.

    Output structure:
      <output>          — copy of the base binary (executable)
      <output>.identity — kbt_identity.json (read by binary on startup)

    The binary searches for kbt_identity.json in these locations (in order):
      1. _MEIPASS (frozen bundle)
      2. Directory of the executable
      3. Current working directory

    We write the identity alongside the binary so it is found at location 2.
    This keeps the approach simple without needing to re-run PyInstaller per employee.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    # Copy base binary
    shutil.copy2(base_binary, output)
    if base_binary.suffix == "":
        # Make Linux binary executable
        output.chmod(0o755)

    # Write identity as kbt_identity.json beside the binary
    identity_path = output.parent / "kbt_identity.json"
    identity_path.write_text(json.dumps(identity, indent=2), encoding="utf-8")

    print(f"✅ Binary:   {output}")
    print(f"✅ Identity: {identity_path}")
    print()
    print("📦 Distribute BOTH files together (zip recommended).")
    print(f"   Employee runs: ./{output.name}")


def main():
    parser = argparse.ArgumentParser(description="KBT Per-Employee Packager")
    parser.add_argument("--employee-id",  required=True, help="Employee UUID from TBAPS backend")
    parser.add_argument("--api-url",      required=True, help="TBAPS API base URL")
    parser.add_argument("--admin-token",  required=True, help="Admin/Manager/HR JWT for API auth")
    parser.add_argument("--base-binary",  required=True, help="Path to the base KBT binary (dist/KBT or KBT.exe)")
    parser.add_argument("--output",       required=True, help="Output binary path (e.g. dist/employees/KBT_john_doe)")
    args = parser.parse_args()

    base = Path(args.base_binary)
    if not base.exists():
        print(f"❌ Base binary not found: {base}\n   Run build_kbt.sh first.", file=sys.stderr)
        sys.exit(1)

    print(f"🔑 Generating KBT token for employee: {args.employee_id}")
    token_data = _get_kbt_token(args.api_url, args.employee_id, args.admin_token)
    print(f"   Token generated. Expires: {token_data.get('expires_at', 'N/A')}")

    identity = build_identity(token_data, args.api_url)

    output = Path(args.output)
    # Preserve Windows .exe extension
    if base.suffix == ".exe" and output.suffix != ".exe":
        output = output.with_suffix(".exe")

    print(f"\n📦 Packaging binary → {output}")
    package_binary(base, identity, output)

    print(f"\n🔒 Security reminders:")
    print(f"   • This token is unique to employee {args.employee_id}")
    print(f"   • The raw token is shown only once and stored NOWHERE on the server")
    print(f"   • Revoke via: DELETE /api/v1/kbt/revoke/{args.employee_id}")


if __name__ == "__main__":
    main()
