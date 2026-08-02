"""Google Sheets & Excel Logger for ORBIT Operations.

Appends structured records for every case to:
1. `orbit_cases_log.csv` (Excel-compatible structured spreadsheet file).
2. Google Sheets via Google Service Account API when GOOGLE_SHEET_ID is configured.
"""

from __future__ import annotations

import csv
import datetime
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("orbit.sheets_logger")

CSV_FILE_PATH = Path(__file__).resolve().parent.parent / "orbit_cases_log.csv"

CSV_HEADERS = [
    "Timestamp",
    "Case ID",
    "Order ID",
    "Customer Email",
    "Case Type",
    "Problem / Symptom",
    "Inbound Mail Summary",
    "Identified Root Cause",
    "Confidence %",
    "Resolution Action",
    "Responsible Agent",
    "Status",
    "Duration (s)",
]


def _format_root_cause(str_val: str | None) -> str:
    if not str_val:
        return "Unknown Issue"
    cleaned = str_val.replace("payment_hold.", "").replace("inventory_mismatch.", "")
    cleaned = cleaned.replace("customs_block.", "").replace("invoice_dispute.", "")
    cleaned = cleaned.replace("compliance_block.", "").replace("shipment_delay.", "")
    cleaned = cleaned.replace("h_", "").replace("_", " ")
    return cleaned.title()


def _format_agent(case_type: str | None) -> str:
    mapping = {
        "payment_hold": "[Agent: Tally ERP / Bank Recon]",
        "inventory_mismatch": "[Agent: Warehouse / Stock Audit]",
        "customs_block": "[Agent: GST & E-Way Portal]",
        "invoice_dispute": "[Agent: Tally Billing]",
        "compliance_block": "[Agent: Transport Operator Fleet]",
        "shipment_delay": "[Agent: Delhivery Tracking]",
    }
    return mapping.get(case_type or "", "[Agent: Operations Detective]")


def _ensure_csv_headers() -> None:
    """Ensure orbit_cases_log.csv exists and has headers."""
    if not CSV_FILE_PATH.exists() or CSV_FILE_PATH.stat().st_size == 0:
        with open(CSV_FILE_PATH, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def log_case_to_sheet(info: dict[str, Any]) -> dict[str, Any]:
    """Record a completed case into local Excel CSV and Google Sheets."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    case_id = str(info.get("case_id", "—"))
    order_id = str(info.get("order_id", "—"))
    from actions.gmail_drafter import _buyer_email
    email = _buyer_email(info.get("sender") or info.get("customer_email"), order_id)
    
    # Extract case_type from root_cause if not provided directly
    root_cause_raw = info.get("root_cause")
    case_type = info.get("case_type")
    if not case_type and root_cause_raw and "." in str(root_cause_raw):
        case_type = str(root_cause_raw).split(".")[0]
    case_type = str(case_type or "operations")
    
    symptom = str(info.get("symptom") or "—")
    summary = str(info.get("summary") or symptom)
    root_cause = _format_root_cause(root_cause_raw)

    confidence = info.get("confidence")
    if confidence is not None:
        try:
            conf_str = f"{int(float(confidence) * 100)}%"
        except (ValueError, TypeError):
            conf_str = str(confidence)
    else:
        conf_str = "—"

    action = str(info.get("action") or "—")
    agent = _format_agent(case_type)
    status = str(info.get("status") or "CLOSED").upper()
    duration = str(info.get("wall_clock_s") or info.get("duration") or "—")

    row = [
        now_str,
        case_id,
        order_id,
        email,
        case_type.replace("_", " ").title(),
        symptom,
        summary,
        root_cause,
        conf_str,
        action,
        agent,
        status,
        duration,
    ]

    # 1. Append to local Excel CSV
    try:
        _ensure_csv_headers()
        with open(CSV_FILE_PATH, mode="a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(row)
        log.info(f"Logged case {case_id} to Excel CSV ({CSV_FILE_PATH.name})")
    except Exception as exc:
        log.warning(f"Failed to write to Excel CSV: {exc}")

    # 2. Sync to Google Sheets if configured
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    secrets_file = Path(__file__).resolve().parent.parent / ".secrets" / "service_account.json"

    if sheet_id and secrets_file.exists():
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = Credentials.from_service_account_file(str(secrets_file), scopes=scopes)
            service = build("sheets", "v4", credentials=creds)

            body = {"values": [row]}
            service.spreadsheets().values().append(
                spreadsheetId=sheet_id,
                range="A1",
                valueInputOption="USER_ENTERED",
                body=body,
            ).execute()
            log.info(f"Logged case {case_id} to Google Sheet {sheet_id}")
        except Exception as exc:
            log.warning(f"Google Sheets sync skipped / failed: {exc}")

    return {"status": "logged", "csv_file": str(CSV_FILE_PATH), "row": row}
