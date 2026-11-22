"""Public IAQuote application package.

Keeping the factory at package level makes ``from app import create_app`` work
both in GitHub Actions and when the project is run from a source checkout.
"""
from .app import create_app

__all__ = ["create_app"]
__version__ = "0.3.2"
