"""AgencyOS Secure File Upload Utility with Cloudflare R2 / S3 Support.

Handles secure file validation, size limits (10MB cap), local storage fallback,
and S3/Cloudflare R2 cloud object storage integration.
"""

import os
import secrets
from werkzeug.utils import secure_filename
from flask import current_app


def save_project_upload(file_storage, allowed_extensions: set[str]) -> str | None:
    """Securely validate and save an uploaded file to local disk or Cloudflare R2 / S3."""
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""

    if ext not in allowed_extensions:
        raise ValueError(
            f"Invalid file extension '.{ext}'. Allowed types: {', '.join(sorted(allowed_extensions))}."
        )

    file_storage.seek(0, os.SEEK_END)
    size_bytes = file_storage.tell()
    file_storage.seek(0)

    if size_bytes > 10 * 1024 * 1024:
        raise ValueError("File size exceeds the 10 MB maximum limit.")

    unique_prefix = secrets.token_hex(16)
    unique_filename = f"{unique_prefix}_{filename}"

    r2_access_key = os.getenv("R2_ACCESS_KEY_ID")
    r2_secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    r2_bucket = os.getenv("R2_BUCKET_NAME")
    r2_endpoint = os.getenv("R2_ENDPOINT_URL")

    if r2_access_key and r2_secret_key and r2_bucket and r2_endpoint:
        try:
            import boto3  # pyright: ignore [reportMissingImports]
            session = boto3.Session()
            client = session.client(
                "s3",
                endpoint_url=r2_endpoint,
                aws_access_key_id=r2_access_key,
                aws_secret_access_key=r2_secret_key,
                region_name="auto",
            )
            client.upload_fileobj(
                file_storage,
                r2_bucket,
                unique_filename,
                ExtraArgs={"ContentType": file_storage.content_type or "application/octet-stream"},
            )
            public_domain = os.getenv("R2_PUBLIC_DOMAIN", r2_endpoint.rstrip("/"))
            return f"{public_domain}/{r2_bucket}/{unique_filename}"
        except Exception as exc:
            current_app.logger.warning("Cloudflare R2 upload failed; falling back to local storage: %s", exc)

    static_folder = current_app.static_folder or os.path.abspath(
        os.path.join(current_app.root_path, "..", "static")
    )
    upload_folder = os.path.join(str(static_folder), "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    destination = os.path.join(upload_folder, unique_filename)
    file_storage.save(destination)

    return f"/static/uploads/{unique_filename}"