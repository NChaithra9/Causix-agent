"""Hand-built graph fixture for Phase 5 impact-analysis tests.

Not a test module (leading underscore). The Phase 2 mapper only produces
Repository / File / Class / Method / Function / Commit nodes, so Service,
API, Event, Database, Table and Test nodes -- which the schema declares but
nothing yet ingests -- are written here directly through the real
``ingest_batch`` (it is label-generic), using the real schema labels and
relationship names. Node ids are readable ("Method:PaymentService.process_payment")
instead of hashes so assertions stay legible.
"""

from __future__ import annotations

from src.graph import GraphBatch, NodeLabel, NodeRecord, RelationshipRecord, ingest_batch
from src.graph.connection import Neo4jConnection


class Fixture:
    def __init__(self, repo_name: str = "payments-service") -> None:
        self.batch = GraphBatch()
        self._labels: dict[str, NodeLabel] = {}
        self.repo = self._node(NodeLabel.REPOSITORY, repo_name, name=repo_name, root_path=f"/{repo_name}")
        self._files: dict[str, str] = {}
        self._classes: dict[str, str] = {}

    # ---- helpers ----
    def _node(self, label: NodeLabel, key: str, **props) -> str:
        node_id = f"{label.value}:{key}"
        self._labels[node_id] = label
        self.batch.nodes.append(NodeRecord(label=label, id=node_id, properties=props))
        return node_id

    def link(self, start: str, rel_type: str, end: str) -> None:
        self.batch.relationships.append(
            RelationshipRecord(self._labels[start], start, rel_type, self._labels[end], end)
        )

    # ---- code ----
    def file(self, path: str) -> str:
        if path not in self._files:
            fid = self._node(NodeLabel.FILE, path, path=path, name=path.rsplit("/", 1)[-1], repository_id=self.repo)
            self._files[path] = fid
            self.link(self.repo, "CONTAINS", fid)
        return self._files[path]

    def klass(self, name: str, file_path: str) -> str:
        fid = self.file(file_path)
        cid = self._node(NodeLabel.CLASS, name, name=name, file_id=fid, line_number=1)
        self._classes[name] = cid
        self.link(fid, "CONTAINS", cid)
        return cid

    def method(self, klass: str, name: str, unresolved_calls: list[str] | None = None) -> str:
        cid = self._classes[klass]
        file_id = next(n.properties["file_id"] for n in self.batch.nodes if n.id == cid)
        mid = self._node(NodeLabel.METHOD, f"{klass}.{name}", name=name, class_id=cid, file_id=file_id,
                         line_number=1, unresolved_calls=unresolved_calls or [])
        self.link(cid, "CONTAINS", mid)
        return mid

    def function(self, name: str, file_path: str, unresolved_calls: list[str] | None = None) -> str:
        fid = self.file(file_path)
        nid = self._node(NodeLabel.FUNCTION, f"{file_path}::{name}", name=name, file_id=fid, line_number=1,
                         unresolved_calls=unresolved_calls or [])
        self.link(fid, "CONTAINS", nid)
        return nid

    def calls(self, caller: str, callee: str) -> None:
        self.link(caller, "CALLS", callee)

    # ---- architecture (declared by the schema, not yet ingested by the mapper) ----
    def service(self, name: str) -> str:
        return self._node(NodeLabel.SERVICE, name, name=name)

    def api(self, name: str) -> str:
        return self._node(NodeLabel.API, name, name=name)

    def event(self, name: str) -> str:
        return self._node(NodeLabel.EVENT, name, name=name)

    def database(self, name: str) -> str:
        return self._node(NodeLabel.DATABASE, name, name=name)

    def table(self, name: str) -> str:
        return self._node(NodeLabel.TABLE, name, name=name)

    def test(self, name: str, path: str) -> str:
        return self._node(NodeLabel.TEST, name, name=name, path=path)

    def ingest(self, connection: Neo4jConnection) -> None:
        ingest_batch(connection, self.batch)


def names(items) -> list[str]:
    return [i.name for i in items]


def checkout_fixture() -> Fixture:
    """The spec's end-to-end graph:

        POST /checkout <-EXPOSES- CheckoutController.checkout
            -CALLS-> PaymentService.process_payment
            -CALLS-> PaymentValidator.validate_payment
        PaymentService -PUBLISHES-> PaymentProcessed ; -ACCESSES-> PaymentsDatabase -CONTAINS-> payments
        test_validate_payment -VALIDATES-> PaymentValidator.validate_payment
    """
    fx = Fixture()
    fx.klass("PaymentValidator", "payment/validator.py")
    fx.klass("PaymentService", "payment/service.py")
    fx.klass("CheckoutController", "controllers/checkout_controller.py")
    validate = fx.method("PaymentValidator", "validate_payment")
    process = fx.method("PaymentService", "process_payment")
    checkout = fx.method("CheckoutController", "checkout")
    fx.calls(process, validate)
    fx.calls(checkout, process)

    fx.link(checkout, "EXPOSES", fx.api("POST /checkout"))
    fx.link(fx._classes["PaymentService"], "PUBLISHES", fx.event("PaymentProcessed"))
    db = fx.database("PaymentsDatabase")
    fx.link(fx._classes["PaymentService"], "ACCESSES", db)
    fx.link(db, "CONTAINS", fx.table("payments"))
    fx.link(fx.test("test_validate_payment", "tests/test_validator.py"), "VALIDATES", validate)
    return fx
