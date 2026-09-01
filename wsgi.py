"""Gunicorn entry point."""
from app.main import app

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threaded=True)
