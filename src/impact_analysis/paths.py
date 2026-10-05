"""Impact-path bookkeeping -- Phase 5, requirements 13, 16 and 20.

Two small, pure helpers (no database access):

* :func:`enumerate_chains` reconstructs every shortest route from the
  changed code to a node out of the predecessor map built by
  ``code_graph.build_caller_graph`` -- so two different routes to the same
  caller (A->B->D and A->C->D) both survive, instead of collapsing into one.
* :class:`PathCollector` accumulates the final ``ImpactPath`` list, keeping
  *separate* paths per terminal element (two APIs reached through the same
  service stay two paths), with a per-element cap so a densely connected
  graph cannot produce unbounded output. Hitting the cap is recorded, not
  silent.
"""

from __future__ import annotations

from .models import ImpactCategory, ImpactPath, PathStep

__all__ = ["enumerate_chains", "PathCollector"]


def enumerate_chains(
    node_id: str, start_id: str, predecessors: dict[str, list[str]], limit: int
) -> tuple[list[list[str]], bool]:
    """All shortest chains ``[start, ..., node_id]``, at most ``limit`` of them.

    Returns ``(chains, truncated)``. Terminates on cyclic graphs because
    ``predecessors`` only ever points one level closer to ``start_id``.
    """
    memo: dict[str, list[list[str]]] = {}
    truncated = False

    def chains_to(node: str) -> list[list[str]]:
        nonlocal truncated
        if node in memo:
            return memo[node]
        if node == start_id:
            memo[node] = [[start_id]]
            return memo[node]
        out: list[list[str]] = []
        for pred in predecessors.get(node, []):
            if len(out) >= limit:
                truncated = True
                break
            for chain in chains_to(pred):
                if len(out) >= limit:
                    truncated = True
                    break
                out.append(chain + [node])
        memo[node] = out
        return out

    return chains_to(node_id), truncated


class PathCollector:
    """Collects :class:`ImpactPath` objects, capped per terminal element."""

    def __init__(self, max_per_terminal: int) -> None:
        self.max_per_terminal = max(max_per_terminal, 1)
        self.paths: list[ImpactPath] = []
        self._count: dict[tuple[ImpactCategory, str], int] = {}
        self._seen: set[tuple[ImpactCategory, str, tuple[tuple[str, str | None], ...]]] = set()
        self.capped_terminals: set[tuple[ImpactCategory, str]] = set()

    def add(self, terminal_id: str, category: ImpactCategory, steps: list[PathStep]) -> bool:
        """Add one path. Returns False if it was a duplicate or hit the cap."""
        key = (category, terminal_id)
        signature = tuple((s.node_id, s.relationship) for s in steps)
        if (category, terminal_id, signature) in self._seen:
            return False
        if self._count.get(key, 0) >= self.max_per_terminal:
            self.capped_terminals.add(key)
            return False
        self._seen.add((category, terminal_id, signature))
        self._count[key] = self._count.get(key, 0) + 1
        self.paths.append(ImpactPath(terminal_id=terminal_id, category=category, steps=list(steps)))
        return True
