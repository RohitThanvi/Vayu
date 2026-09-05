"""
rate_limit.py — a single shared slowapi Limiter instance.

Split into its own module because main.py needs it (to register the
exception handler and hold it on app.state) and api/endpoints.py needs
it too (to decorate /query) — main.py already imports endpoints.py, so
endpoints.py importing the limiter back from main.py would be a
circular import. Both import it from here instead.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
