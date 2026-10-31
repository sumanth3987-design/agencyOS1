# Save this directly into your file: app.py

import os
from app import create_app
from app.config import resolve_flask_env

config_name = resolve_flask_env()
app = create_app(config_name)

# Expose 'application' alias for standard WSGI servers (Gunicorn, uWSGI, Render, Railway)
application = app

if __name__ == "__main__":
    debug = config_name == "development" and bool(app.config.get("DEBUG", False))
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", 5000)))

    app.run(
        host=host,
        port=port,
        debug=debug
    )