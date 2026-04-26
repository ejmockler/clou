"""Static AST check: no `*.md` filename literals in phase-acceptance
code paths.

The phase-acceptance gate (``clou/phase_acceptance.py``) is the
sole completion classifier under M52.  Its correctness must
depend only on the bytes it receives — never on filename strings.
This test enforces that invariant by walking the gate module's
AST and refusing any string literal matching ``\\*.md``.

Per F36 + F39, the AST scope is narrow: only
``clou/phase_acceptance.py`` is checked.  Other modules in
``clou/`` may use ``*.md`` literals freely (e.g. the artifact
parser refers to ``execution.md`` as a search-path constant via
the registry, and the coordinator does its own file reads).  The
structural defense is the gate's typed signature: it accepts
pre-read text, not paths, so filename construction physically
cannot happen inside the gate.

If the gate ever reads files directly (regression), this test
fires and pins the violation.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from clou.artifacts import PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS

_GATE_MODULE = (
    Path(__file__).resolve().parent.parent
    / "clou"
    / "phase_acceptance.py"
)

# Match ``*.md`` filename literals.  The pattern is intentionally
# narrow: it must be a clear filename token (word chars, hyphens,
# underscores) followed by ``.md`` and a word boundary.
_MD_FILENAME_RE = re.compile(r"\b[\w\-]+\.md\b")


class _MDLiteralFinder(ast.NodeVisitor):
    """Walk a module AST and collect every ``*.md`` string
    literal outside the data-derived allowlist.

    Skips docstrings (the first ``Expr(Constant(str))`` of a
    module, class, or function body).  Docstrings are prose,
    not load-bearing — references to ``phase.md`` /
    ``execution.md`` in documentation describe the architecture
    rather than constructing paths."""

    def __init__(self) -> None:
        self.violations: list[tuple[str, int]] = []
        self._docstring_constants: set[int] = set()

    def _record_docstring(self, body: list[ast.stmt]) -> None:
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            self._docstring_constants.add(id(body[0].value))

    def visit_Module(self, node: ast.Module) -> None:
        self._record_docstring(node.body)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_docstring(node.body)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(
        self, node: ast.AsyncFunctionDef,
    ) -> None:
        self._record_docstring(node.body)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record_docstring(node.body)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) in self._docstring_constants:
            return
        if isinstance(node.value, str):
            for match in _MD_FILENAME_RE.finditer(node.value):
                literal = match.group(0)
                if literal in PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS:
                    continue
                self.violations.append((literal, node.lineno))
        self.generic_visit(node)


def test_phase_acceptance_module_has_no_filename_literals_outside_allowlist() -> None:
    """``clou/phase_acceptance.py`` must not contain ``*.md``
    filename literals outside the data-derived allowlist
    (``PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS``)."""
    assert _GATE_MODULE.exists(), f"gate module missing: {_GATE_MODULE}"
    source = _GATE_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    finder = _MDLiteralFinder()
    finder.visit(tree)
    assert not finder.violations, (
        "filename literals found in phase-acceptance code path "
        "outside the data-derived allowlist:\n"
        + "\n".join(
            f"  - {literal!r} at line {lineno}"
            for literal, lineno in finder.violations
        )
        + f"\n\nAllowlist: {sorted(PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS)}"
    )


def test_gate_signature_accepts_text_not_path() -> None:
    """Per F39, the gate's signature accepts pre-read text, not
    a path.  This is the structural defense — the AST check
    above can stay narrow because the gate cannot itself
    construct paths.

    Verifies by introspection: ``check_phase_acceptance`` has a
    parameter named ``execution_md_text`` (typed as ``str``)
    and does NOT have any path-typed parameter."""
    import inspect

    from clou.phase_acceptance import check_phase_acceptance

    sig = inspect.signature(check_phase_acceptance)
    param_names = list(sig.parameters.keys())
    assert "execution_md_text" in param_names, (
        f"gate is missing the typed-bytes parameter; "
        f"signature: {sig}"
    )
    suspect = {p for p in param_names if p.endswith(("_path", "_dir"))}
    assert not suspect, (
        f"gate has path-typed parameters that violate F39: {suspect}"
    )


def test_meta_finder_catches_planted_violation() -> None:
    """Meta-test: if the AST finder regresses, the structural
    invariant gives false confidence.  Synthesise a tiny module
    with a planted ``*.md`` literal outside the allowlist and
    verify the finder flags it."""
    fake_module = '''
def f():
    return "spec.md"

def g():
    return "execution.md"  # in allowlist
'''
    tree = ast.parse(fake_module)
    finder = _MDLiteralFinder()
    finder.visit(tree)
    flagged = {literal for literal, _ in finder.violations}
    assert "spec.md" in flagged, (
        f"AST finder regressed — should have flagged 'spec.md', "
        f"got {flagged}"
    )
    assert "execution.md" not in flagged, (
        "AST finder over-rejected — 'execution.md' is in the "
        "data-derived allowlist and should NOT be flagged"
    )
