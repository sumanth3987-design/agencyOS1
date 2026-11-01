# Save this directly into your file: app.py

import os
import sys

# Ensure root directory is top of Python search path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import create_app

# Initialize the Flask application factory instance
app = create_app()
application = app

if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", 5000)))
    app.run(
        host=host,
        port=port,
        debug=True
    )