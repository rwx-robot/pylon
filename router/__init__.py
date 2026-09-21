"""pylon.router - Routing tree and decorator-based registration.

Uses Radix Tree for efficient O(k) route matching where k is path length.
"""

from pylon.router.radix import Router, RouterGroup, RadixTree

__all__ = ["Router", "RouterGroup", "RadixTree"]
