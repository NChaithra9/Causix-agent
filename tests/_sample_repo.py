"""Shared fixture builder for Phase 2 graph tests.

Not a test module itself (leading underscore keeps pytest from collecting
it) -- builds the small sample repository from the Phase 2 spec's
end-to-end example:

    payment/
        __init__.py
        service.py      -- PaymentService.process() calls validate_payment()
        validator.py    -- validate_payment()
    tests/
        test_service.py

with two commits, then runs the full Phase 1 pipeline over it.
"""

from __future__ import annotations

from pathlib import Path

from git import Repo

from src.repository import Repository, build_repository_facts


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def build_sample_repository(root: Path) -> Repository:
    repo = Repo.init(root)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test Author")
        config.set_value("user", "email", "test@example.com")

    _write(root / "payment" / "__init__.py", "")
    _write(root / "payment" / "validator.py", "def validate_payment():\n    pass\n")
    _write(
        root / "payment" / "service.py",
        "from .validator import validate_payment\n\n\n"
        "class PaymentService:\n\n"
        "    def process(self):\n"
        "        validate_payment()\n",
    )
    repo.index.add(["payment/__init__.py", "payment/validator.py", "payment/service.py"])
    repo.index.commit("Add payment service")

    _write(
        root / "tests" / "test_service.py",
        "from payment.service import PaymentService\n\n\ndef test_process():\n    PaymentService().process()\n",
    )
    repo.index.add(["tests/test_service.py"])
    repo.index.commit("Add service test")

    return build_repository_facts(root)
