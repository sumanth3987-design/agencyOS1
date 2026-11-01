# Save this directly into your file: app.py

import os
import sys

# Ensure root directory is top of search path
base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

# Import the running app from run.py cleanly
import run

app = run.app
application = app

if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", 5000)))
    app.run(
        host=host,
        port=port,
        debug=True
    )