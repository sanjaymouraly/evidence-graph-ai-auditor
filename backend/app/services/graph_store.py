"""Graph storage abstraction.

The default `NetworkXGraphStore` projects persisted relational graph records into an
in-memory NetworkX graph for traversal and impact analysis. `Neo4jGraphStore` is an
optional adapter for larger deployments and requires the `neo4j` extra plus explicit
configuration; it never silently falls back.
"""
from __future__ import annotations

from typing import Protocol

import networkx as nx


class GraphStore(Protocol):
    def add_node(self, key: str, **attrs) -> None: ...
    def add_edge(self, source: str, target: str, **attrs) -> None: ...
    def neighbors(self, key: str) -> list[str]: ...
    def shortest_path(self, source: str, target: str) -> list[str]: ...
    def subgraph_within(self, roots: list[str], depth: int) -> set[str]: ...
    def to_networkx(self) -> nx.Graph: ...


class NetworkXGraphStore:
    def __init__(self) -> None:
        self.graph = nx.Graph()

    def add_node(self, key: str, **attrs) -> None:
        self.graph.add_node(key, **attrs)

    def add_edge(self, source: str, target: str, **attrs) -> None:
        if not self.graph.has_node(source):
            self.graph.add_node(source)
        if not self.graph.has_node(target):
            self.graph.add_node(target)
        self.graph.add_edge(source, target, **attrs)

    def neighbors(self, key: str) -> list[str]:
        return list(self.graph.neighbors(key)) if self.graph.has_node(key) else []

    def shortest_path(self, source: str, target: str) -> list[str]:
        try:
            return nx.shortest_path(self.graph, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def subgraph_within(self, roots: list[str], depth: int) -> set[str]:
        seen: set[str] = set()
        frontier = [r for r in roots if self.graph.has_node(r)]
        seen.update(frontier)
        for _ in range(max(0, depth)):
            nxt = []
            for node in frontier:
                for neighbor in self.graph.neighbors(node):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        nxt.append(neighbor)
            frontier = nxt
            if not frontier:
                break
        return seen

    def to_networkx(self) -> nx.Graph:
        return self.graph


class Neo4jGraphStore:
    """Optional Neo4j adapter stub.

    Not wired into the application: `services/graph.py` uses NetworkXGraphStore. Implementing
    the GraphStore protocol here and adding a factory is the work required to make this
    selectable.
    """

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        try:
            from neo4j import GraphDatabase  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("The 'neo4j' package is required for Neo4jGraphStore. "
                               "Install it with `pip install neo4j`; the application itself "
                               "runs on the networkx store.") from exc
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def projection_query(self) -> str:
        return ("MATCH (n) WHERE n.workspace_id = $workspace_id RETURN n, "
                "MATCH (n)-[r]->(m) WHERE r.workspace_id = $workspace_id RETURN n, r, m")

    def close(self) -> None:
        self.driver.close()
