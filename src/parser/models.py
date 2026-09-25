"""Plain data models for facts extracted from Python source files.

These dataclasses are just typed containers -- they don't do any parsing
themselves. See ``python_parser.py`` for the code that fills them in using
the ``ast`` module.

Keeping the models separate from the parsing logic makes it easy for later
steps (e.g. building a Neo4j graph) to consume these objects without caring
how they were produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class ImportInfo:
    """A single imported name, covering every common Python import style.

    Attributes:
        module: The module path an import is *from*. ``None`` for a plain
            ``import x`` (there, ``name`` itself is the module). Relative
            imports (``from . import x`` / ``from ..foo import x``) keep
            their leading dots in this string (``"."`` / ``"..foo"``).
        name: The imported name -- a module for ``import x``, or an
            attribute for ``from x import y``.
        alias: The ``as`` alias, if one was used, else ``None``.
        line: 1-based source line number where the import statement starts.
    """

    module: str | None
    name: str
    alias: str | None
    line: int

    @property
    def qualified_name(self) -> str:
        """Dotted path this import refers to, e.g. ``payment_service.get_payment``."""
        if self.module:
            return f"{self.module}.{self.name}"
        return self.name


@dataclass
class FunctionInfo:
    """A top-level (module-level) function definition."""

    name: str
    line: int
    args: list[str] = field(default_factory=list)
    is_async: bool = False


@dataclass
class MethodInfo:
    """A function defined directly inside a class body."""

    name: str
    class_name: str
    line: int
    args: list[str] = field(default_factory=list)
    is_async: bool = False

    @property
    def qualified_name(self) -> str:
        """Dotted name, e.g. ``RefundService.check_refund``."""
        return f"{self.class_name}.{self.name}"


@dataclass
class ClassInfo:
    """A class definition and the methods found directly in its body."""

    name: str
    line: int
    methods: list[MethodInfo] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)


@dataclass
class ParsedFile:
    """Everything extracted from one Python source file.

    ``error`` is set (and everything else left empty) when the file could
    not be parsed, e.g. because of a syntax error -- this lets a whole
    repository scan continue past one bad file instead of crashing.
    """

    file_path: str
    imports: list[ImportInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Step 5 -- relationships between the code elements modeled above
# ---------------------------------------------------------------------------


class RelationshipType(str, Enum):
    """The kinds of deterministic relationships Step 5 can extract.

    An enum (not scattered string literals) so relationship types are
    validated, easy to compare, and read the same everywhere they're used.
    Subclassing ``str`` keeps it easy to print/serialize (e.g. towards Neo4j
    later) without extra conversion.
    """

    CONTAINS = "CONTAINS"
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"  # File -> IMPORTS -> File, when an import resolves to another parsed file in the repo
    MODIFIES = "MODIFIES"  # Commit -> MODIFIES -> File (Git history, Phase 1 requirement 7)


@dataclass
class Relationship:
    """One deterministic fact linking two code elements.

    Attributes:
        source: The qualified name of the element the relationship starts
            from -- a file path (for CONTAINS from a file), a class name,
            or a function/method's qualified name (e.g.
            ``RefundService.process``).
        relationship_type: One of :class:`RelationshipType`.
        target: The qualified name of the element the relationship points
            to. For an unresolved CALLS relationship, this is the raw
            name/expression exactly as written in the source (e.g.
            ``service.process`` or ``external_unknown_function``) -- never
            a guessed or invented target.
        source_file: The file the relationship was found in.
        line: 1-based source line number the relationship was found at.
        resolved: Whether ``target`` is confirmed to point at another
            element Step 4 actually found. Always ``True`` for CONTAINS
            relationships, since those are derived directly from Step 4's
            own structural facts with no ambiguity involved.
        raw_call: For a CALLS relationship, the call expression exactly as
            written in the source (e.g. ``service.process()``). ``None``
            for CONTAINS relationships, where there's no call expression.
    """

    source: str
    relationship_type: RelationshipType
    target: str
    source_file: str
    line: int
    resolved: bool = True
    raw_call: str | None = None


@dataclass
class SourceFileInfo:
    """Lightweight metadata about one discovered source file.

    Distinct from :class:`ParsedFile`: this describes a file that was found
    while scanning a repository, independent of whether it has been (or
    even can be) parsed. See ``python_parser.scan_source_files``.
    """

    absolute_path: str
    relative_path: str
    file_name: str
    language: str
    line_count: int
