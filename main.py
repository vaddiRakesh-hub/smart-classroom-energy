"""Entrypoint alias for platforms looking for main.py."""
import os
from app import app, bootstrap

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
