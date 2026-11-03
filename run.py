"""Local Development Runner for AgencyOS."""

import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", 5000)))
    app.run(
        host=host,
        port=port,
        debug=True,
    )