"""`uvicorn bridge.main:app`。"""

from bridge.app import create_app

app = create_app()
