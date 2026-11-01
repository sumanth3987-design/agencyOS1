import os
import uuid

from flask import current_app
from werkzeug.utils import secure_filename


MAX_PROJECT_UPLOAD_SIZE = 10 * 1024 * 1024


def save_project_upload(upload, allowed_extensions):
    if upload is None or not upload.filename:
        return None

    filename = secure_filename(upload.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in allowed_extensions:
        raise ValueError("This file type is not permitted.")

    stem, suffix = os.path.splitext(filename)
    stored_name = f"{uuid.uuid4().hex}_{stem[:120]}{suffix.lower()}"
    upload_dir = os.path.join(current_app.static_folder, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    file_path = os.path.join(upload_dir, stored_name)
    saved = False
    try:
        size = 0
        with open(file_path, "wb") as saved_file:
            while True:
                chunk = upload.stream.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_PROJECT_UPLOAD_SIZE:
                    raise ValueError("Files must be 10 MB or smaller.")
                saved_file.write(chunk)
        saved = True
    finally:
        if not saved and os.path.exists(file_path):
            os.remove(file_path)

    return f"/static/uploads/{stored_name}"
