"""
pylon.router.radix - Radix Tree (compressed prefix tree) router.

A radix tree is a space-optimized trie where each node with only one
child is merged with its child. This provides O(k) lookup where k is
the length of the path, compared to O(n) for linear search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


Handler = Callable[..., Awaitable[Any]]


@dataclass
class RadixNode:
    """A node in the radix tree."""

    # Path segment this node represents (e.g., "users", ":id")
    segment: str = ""

    # Is this a parameter node? (starts with :)
    is_param: bool = False

    # Parameter name if is_param is True
    param_name: Optional[str] = None

    # Handlers registered at this node, keyed by HTTP method
    handlers: Dict[str, Handler] = field(default_factory=dict)

    # Children nodes
    children: Dict[str, RadixNode] = field(default_factory=dict)

    # For conflict detection: list of conflicting paths
    conflicts: List[str] = field(default_factory=list)


class RadixTree:
    """
    Radix tree router for efficient path matching.

    Supports:
    - Static routes: /users, /api/v1/users
    - Parameter routes: /users/:id, /items/:category/:id
    - Wildcard routes: /files/*filepath
    - Multiple methods per path: GET/POST/PUT/DELETE on same route
    """

    def __init__(self):
        self._root = RadixNode(segment="")
        self._routes: List[Tuple[str, str, Handler]] = []  # (method, path, handler)

    def add_route(
        self,
        method: str,
        path: str,
        handler: Handler,
    ) -> None:
        """Add a route to the radix tree."""
        self._routes.append((method, path, handler))

        # Split path into segments
        segments = self._split_path(path)
        self._insert(self._root, method, segments, handler)

    def _split_path(self, path: str) -> List[Tuple[str, bool, Optional[str]]]:
        """
        Split path into segments with metadata.

        Returns list of (segment, is_param, param_name).
        """
        segments = []
        for part in path.split("/"):
            if not part:
                continue
            if part.startswith(":"):
                segments.append((part, True, part[1:]))
            elif part.startswith("*"):
                # Wildcard: * or *name captures remaining path
                segments.append((part, False, None))  # is_wildcard=True
            else:
                segments.append((part, False, None))
        return segments

    def _insert(
        self,
        node: RadixNode,
        method: str,
        segments: List[Tuple[str, bool, Optional[str]]],
        handler: Handler,
    ) -> None:
        """Insert a route into the radix tree."""
        if not segments:
            # Terminal node - register handler for this method
            if method in node.handlers:
                # Same method + path already registered - record conflict
                node.conflicts.append(f"{method} {self._get_path(node)}")
            node.handlers[method] = handler
            return

        segment, is_param, param_name = segments[0]
        remaining = segments[1:]

        if is_param:
            # Parameter node - use special key
            param_key = "::param::"
            if param_key not in node.children:
                node.children[param_key] = RadixNode(
                    segment=segment,
                    is_param=True,
                    param_name=param_name,
                )
            self._insert(node.children[param_key], method, remaining, handler)

        elif segment.startswith("*"):
            # Wildcard node - wildcard captures ALL remaining segments
            wildcard_key = "::wildcard::"
            if wildcard_key not in node.children:
                node.children[wildcard_key] = RadixNode(segment=segment)
            # Wildcard is terminal - register handler at this node
            if method in node.children[wildcard_key].handlers:
                node.children[wildcard_key].conflicts.append(
                    f"{method} wildcard at {self._get_path(node.children[wildcard_key])}"
                )
            node.children[wildcard_key].handlers[method] = handler

        else:
            # Static segment
            if segment not in node.children:
                node.children[segment] = RadixNode(segment=segment)

            # Find common prefix and split if needed
            child = node.children[segment]
            common_len = self._common_prefix_len(segment, child.segment)

            if common_len < len(segment) and common_len < len(child.segment):
                # Need to split the child node
                self._split_node(child, common_len, method, remaining, handler, segment)
            elif common_len < len(child.segment):
                # Segment is a prefix of existing child segment
                self._insert(child, method, [(segment, False, None)] + remaining, handler)
            else:
                # Full match, continue insertion
                self._insert(child, method, remaining, handler)

    def _split_node(
        self,
        node: RadixNode,
        split_at: int,
        method: str,
        remaining: List[Tuple[str, bool, Optional[str]]],
        handler: Handler,
        new_segment: str,
    ) -> None:
        """Split a node at the given position."""
        # Create new parent node with shared prefix
        shared_prefix = node.segment[:split_at]
        new_parent = RadixNode(segment=shared_prefix)

        # Update original child to have the remaining part
        node.segment = node.segment[split_at:]
        new_parent.children[node.segment] = node

        # Create new child for the new route
        new_child = RadixNode(segment=new_segment[split_at:])
        if remaining:
            self._insert(new_child, method, remaining, handler)
        else:
            new_child.handlers[method] = handler

        new_parent.children[new_segment[split_at:]] = new_child

    def _common_prefix_len(self, s1: str, s2: str) -> int:
        """Get length of common prefix."""
        length = 0
        for c1, c2 in zip(s1, s2):
            if c1 == c2:
                length += 1
            else:
                break
        return length

    def _get_path(self, node: RadixNode) -> str:
        """Reconstruct path from root to node."""
        parts = []
        current = node
        while current and current.segment:
            if current.is_param:
                parts.append(f":{current.param_name}")
            else:
                parts.append(current.segment)
            # Find parent... this is simplified
            break  # Would need parent reference for full path
        return "/" + "/".join(parts)

    def match(self, method: str, path: str) -> Optional[Tuple[Handler, Dict[str, str]]]:
        """Match a path against the radix tree. Returns (handler, params) or None."""
        segments = [s for s in path.split("/") if s]
        params: Dict[str, str] = {}
        return self._match_recursive(self._root, method, segments, params)

    def _match_recursive(
        self,
        node: RadixNode,
        method: str,
        segments: List[str],
        params: Dict[str, str],
    ) -> Optional[Tuple[Handler, Dict[str, str]]]:
        """Recursively match segments against the tree."""
        if not segments:
            # End of path - check if this node has a handler for this method
            if method in node.handlers:
                return node.handlers[method], params
            # Check for wildcard match in children (empty path can match wildcard)
            if "::wildcard::" in node.children:
                wc_node = node.children["::wildcard::"]
                if method in wc_node.handlers:
                    return wc_node.handlers[method], params
            return None

        segment = segments[0]
        remaining = segments[1:]

        # Check wildcard match FIRST (wildcard captures this segment and all remaining)
        # This must come before exact/param match so /*filepath matches /a/b/c
        if "::wildcard::" in node.children:
            wc_node = node.children["::wildcard::"]
            if method in wc_node.handlers:
                params_copy = dict(params)
                # Wildcard captures ALL remaining segments including current
                params_copy["*"] = "/".join(segments)
                return wc_node.handlers[method], params_copy

        # Check exact match first
        if segment in node.children:
            child = node.children[segment]
            result = self._match_recursive(child, method, remaining, params)
            if result:
                return result

        # Check parameter match
        if "::param::" in node.children:
            param_node = node.children["::param::"]
            params_copy = dict(params)
            if param_node.param_name:
                params_copy[param_node.param_name] = segment
            result = self._match_recursive(param_node, method, remaining, params_copy)
            if result:
                return result

        return None

    def routes(self) -> List[Tuple[str, str, Handler]]:
        """Return all registered routes."""
        return list(self._routes)

    def print_tree(self, node: Optional[RadixNode] = None, prefix: str = "") -> None:
        """Print the tree structure (debug)."""
        if node is None:
            node = self._root

        for key, child in node.children.items():
            is_param = " [P]" if child.is_param else ""
            is_end = " ✓" if child.handlers else ""
            print(f"{prefix}{key}{is_param}{is_end}")
            self.print_tree(child, prefix + "  ")


# Backward compatibility
class Router:
    """Router with radix tree backing."""

    def __init__(self, prefix: str = ""):
        self.prefix = prefix
        self._tree = RadixTree()
        self._routes: List[Any] = []

    def add_route(self, method: str, path: str, handler: Handler) -> None:
        full_path = self.prefix + path
        self._tree.add_route(method, full_path, handler)
        self._routes.append((method, full_path, handler))

    def get(self, path: str) -> Callable[[Handler], Handler]:
        return self._add_decorator("GET", path)

    def post(self, path: str) -> Callable[[Handler], Handler]:
        return self._add_decorator("POST", path)

    def put(self, path: str) -> Callable[[Handler], Handler]:
        return self._add_decorator("PUT", path)

    def delete(self, path: str) -> Callable[[Handler], Handler]:
        return self._add_decorator("DELETE", path)

    def patch(self, path: str) -> Callable[[Handler], Handler]:
        return self._add_decorator("PATCH", path)

    def options(self, path: str) -> Callable[[Handler], Handler]:
        return self._add_decorator("OPTIONS", path)

    def head(self, path: str) -> Callable[[Handler], Handler]:
        return self._add_decorator("HEAD", path)

    def _add_decorator(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self.add_route(method, path, handler)
            return handler
        return decorator

    def group(self, prefix: str) -> RouterGroup:
        return RouterGroup(self, prefix)

    def match(self, method: str, path: str) -> Optional[Tuple[Handler, Dict[str, str]]]:
        return self._tree.match(method, path)

    def routes(self) -> List[Any]:
        return list(self._routes)


class RouterGroup:
    """A router group with shared prefix."""

    def __init__(self, router: Router, prefix: str):
        self._router = router
        self._prefix = prefix

    def _add(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self._router.add_route(method, self._prefix + path, handler)
            return handler
        return decorator

    def get(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("GET", path)

    def post(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("POST", path)

    def put(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("PUT", path)

    def delete(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("DELETE", path)

    def patch(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("PATCH", path)

    def options(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("OPTIONS", path)

    def head(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("HEAD", path)

    def group(self, prefix: str) -> RouterGroup:
        """Create a nested route group with additional prefix."""
        return RouterGroup(self._router, self._prefix + prefix)
