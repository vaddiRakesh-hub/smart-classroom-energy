"""Root entrypoint for Railway, Render, Docker, and PaaS hosts."""
import importlib.util
import os
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")

# Ensure backend folder is on search path for internal modules (config, db, service, etc.)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Load the Flask app directly from backend/app.py without circular naming collision
_backend_app_path = os.path.join(BACKEND_DIR, "app.py")
_spec = importlib.util.spec_from_file_location("backend_app", _backend_app_path)
_backend_module = importlib.util.module_from_spec(_spec)
sys.modules["backend_app"] = _backend_module
_spec.loader.exec_module(_backend_module)

app = _backend_module.app
bootstrap = _backend_module.bootstrap

# Ensure initial database setup and model loading
bootstrap()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
