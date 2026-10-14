"""Package entry recognized by agents-cli; start with the pinned environment."""
from .api import create_app

app = create_app()
