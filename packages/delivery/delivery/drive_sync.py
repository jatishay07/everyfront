"""Google Drive mirroring -- §4 persona 4 WO6.

"mirror each case's generated filings to a per-case Drive folder
(advocate-shareable)."

One folder per case (`case-{case_id}`) under an optional root folder;
re-running with the same filenames UPDATES the existing Drive file instead
of creating a duplicate, so this is safe to call every time a filing is
generated, not just once.
"""

from __future__ import annotations

import contextlib
import io
import os
from typing import Any

from .google_auth import MissingCredentialsError, load_user_credentials

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _find_or_create_folder(service, name: str, parent_id: str | None) -> str:
    q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    resp = service.files().list(q=q, fields="files(id, name)", spaces="drive").execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]
    body: dict[str, Any] = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    created = service.files().create(body=body, fields="id").execute()
    return created["id"]


def mirror_case_filings(
    case_id: str,
    filings: list[dict[str, Any]],
    *,
    root_folder_id: str | None = None,
    share_with_email: str | None = None,
) -> dict[str, Any] | None:
    """`filings` is `[{"filename": str, "pdf_bytes": bytes, "front": str}, ...]`.

    Returns `{"case_folder_id": ..., "files": [...]}`, or `None` if the demo
    account's OAuth credentials are not configured (see
    `google_auth.MissingCredentialsError`) -- a missing Drive mirror must
    never fail the filing that produced the PDF in the first place.
    """
    try:
        creds = load_user_credentials(DRIVE_SCOPES)
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
    except MissingCredentialsError:
        return None

    service = build("drive", "v3", credentials=creds)
    root_folder_id = root_folder_id or os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID") or None
    case_folder_id = _find_or_create_folder(service, f"case-{case_id}", root_folder_id)

    share_with_email = share_with_email or os.environ.get("GOOGLE_DRIVE_ADVOCATE_EMAIL", "")
    if share_with_email:
        # Sharing is a nice-to-have, not load-bearing -- a permissions error
        # must not stop the filings themselves from being mirrored.
        with contextlib.suppress(Exception):
            service.permissions().create(
                fileId=case_folder_id,
                body={"type": "user", "role": "reader", "emailAddress": share_with_email},
                sendNotificationEmail=False,
            ).execute()

    uploaded: list[dict[str, Any]] = []
    for f in filings:
        filename = f["filename"]
        existing = (
            service.files()
            .list(
                q=f"name = '{filename}' and '{case_folder_id}' in parents and trashed = false",
                fields="files(id)",
            )
            .execute()
            .get("files", [])
        )
        media = MediaIoBaseUpload(
            io.BytesIO(f["pdf_bytes"]), mimetype="application/pdf", resumable=False
        )
        if existing:
            file = (
                service.files()
                .update(fileId=existing[0]["id"], media_body=media, fields="id, webViewLink")
                .execute()
            )
        else:
            file = (
                service.files()
                .create(
                    body={"name": filename, "parents": [case_folder_id]},
                    media_body=media,
                    fields="id, webViewLink",
                )
                .execute()
            )
        uploaded.append(
            {
                "front": f.get("front"),
                "filename": filename,
                "file_id": file["id"],
                "link": file.get("webViewLink"),
            }
        )
    return {"case_folder_id": case_folder_id, "files": uploaded}
