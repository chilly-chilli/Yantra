"""Local entry for uvicorn and agents-cli; never starts research on import."""
from yantra.api import create_app

app = create_app()
