"""Offline cost calculator.

Prices come from a versioned table hashed by file bytes. Token meters use
per-million rates. Local open-source rows use normalized GPU seconds.
Importing this package does not import Inspect AI or LiteLLM, and it does
not fetch a provider price.
"""

from ohbe.cost.engine import ExecutionCost, InvocationCost, price_trace
from ohbe.cost.table import PricingError, PricingTable, load_pricing_table

__all__ = [
    "ExecutionCost",
    "InvocationCost",
    "PricingError",
    "PricingTable",
    "load_pricing_table",
    "price_trace",
]
