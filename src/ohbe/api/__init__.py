"""HTTP surface for the OHBE engine.

``ohbe`` remains the engine. This package adapts that engine to the ``/v1``
routes a future SDK binds. Core does not import this package. Importing it
does not import LiteLLM or Inspect AI, and it does not place a model call.
"""

from ohbe.api.app import create_app

__all__ = ["create_app"]
