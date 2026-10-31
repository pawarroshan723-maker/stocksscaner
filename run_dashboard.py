#!/usr/bin/env python3
"""Run the rebuilt local web dashboard."""
import os
from scanner_web import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
