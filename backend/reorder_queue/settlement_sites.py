"""Derive every site that decides — or reads — whether a purchase-order line is settled.

Six defects of one shape reached the captain before this file existed: some code
changed whether a line counts as settled, or read that fact, and did not go
through the same derivation as its siblings. Each was found and fixed on its
own, and the class kept producing new sites. The last one lived in
``inventory.services.item_metrics`` — a different app from the one being edited
— which is why the sweep commissioned to find every consumer walked past it. It
had derived consumers from the app it happened to be in.

So this does not start from a list of files, or of apps, or of fields. It starts
from the DATA:

1. :func:`derive_anchor` parses ``reorder_queue/models.py`` and walks
   ``PurchaseOrderItem.is_settled`` — the property whose docstring says it is the
   definition — transitively through every member it reads, until only concrete
   model fields are left. That closure IS the settlement definition, and the
   fields it lands on are the settlement fields. Nothing here is hand-listed: add
   a field to the definition and it appears in the closure on the next run;
   rename one and the closure follows it, because a rename is a migration.
2. :func:`scan` then reads every ``.py`` in ``backend/`` and every ``.ts``/
   ``.tsx`` in ``frontend/src`` and reports the sites that touch those fields.

The rule it enforces has one sentence:

    Outside ``PurchaseOrderItem``, no expression may bring two different
    settlement fields together, and none may read a field the definition itself
    never trusts on its own; and any function that can settle a line must
    re-derive the order's status.

"Never trusts on its own" is derived, not asserted. The closure records how each
field is read: ``is_voided`` appears as a bare truth test (``if self.is_voided``),
so its own value IS the answer and asking it elsewhere is a fair question.
``closed_short_at`` and ``reopened_at`` never do — the definition only ever
compares them against each other, because which of the two is in force is the
whole point — so reading either alone anywhere is a site that has already got
the answer wrong. Add a field of that shape later and it joins them without this
file changing.

The first half is deliberately not a name match. A site that re-implements
``quantity_received < quantity_ordered`` by hand references no shared helper and
would never appear in a caller graph — it is caught here because it names two of
the fields in one expression, which is the thing it cannot avoid doing. The
write arm is a name match, but in the safe direction: it *requires* a call to
``refresh_receipt_status``, so writing ``my_own_refresh()`` instead does not
satisfy it.

What this does NOT cover is stated in the module's own report and in
:mod:`reorder_queue.tests.test_settlement_sites`: raw SQL, values_list into
local variables that are then compared, and — on the frontend — anything the
line-based scanner cannot see. Those are holes, not absences of sites.

Run it directly for a report::

    python3 backend/reorder_queue/settlement_sites.py

Exits non-zero when a site bypasses the derivation, which is what
``reorder_queue/tests/test_settlement_sites.py`` asserts on and what CI runs.
Stdlib only, and it imports nothing from Django, so the frontend-lint job can
run it without a backend environment.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: The one seed. ``PurchaseOrderItem.is_settled`` is the authoritative answer to
#: "is receiving finished with this line?" — everything else about settlement is
#: derived by following what it reads.
SEED = "is_settled"

#: The function every settlement WRITE has to reach. Named, not described, so a
#: differently-named re-implementation does not satisfy the requirement.
REFRESH = "refresh_receipt_status"

#: Call names whose arguments are a query PREDICATE — where naming a field means
#: asking a question about it rather than displaying or storing it. ``create``
#: is deliberately absent: it stores, and is covered by the write arm instead.
PREDICATE_CALLS = frozenset(
    {"filter", "exclude", "get", "Q", "update", "annotate", "aggregate", "When"}
)

#: Call names that persist a field value passed as a keyword.
WRITE_CALLS = frozenset({"create", "update", "get_or_create", "update_or_create", "bulk_create"})

_SKIP_DIR_PARTS = ("__pycache__", "node_modules", ".venv", "staticfiles", "media")


def _is_test_path(rel: str) -> bool:
    """Whether a repo-relative path is test scaffolding rather than a live site."""
    parts = rel.split("/")
    name = parts[-1]
    return (
        "tests" in parts
        or "__tests__" in parts
        or name.startswith("test_")
        or name in ("tests.py", "conftest.py", "factories.py")
        or name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )


@dataclass
class Anchor:
    """The authoritative settlement definition, read off the model itself."""

    #: field name -> declared Django field class (e.g. ``quantity_received`` ->
    #: ``PositiveIntegerField``)
    fields: dict[str, str]
    #: Settlement fields that carry a QUANTITY. Two of these in one expression is
    #: a site re-deriving "did what was ordered arrive?".
    quantities: frozenset[str]
    #: Settlement fields that MARK an ending — struck off, written off, taken
    #: back — rather than counting units.
    markers: frozenset[str]
    #: Marker fields the definition never reads as a bare truth test, i.e. ones
    #: whose value alone answers nothing. Reading one outside the class is
    #: already a wrong answer, however it is worded.
    entangled: frozenset[str]
    #: Class members reachable from :data:`SEED` — the derivation itself.
    members: frozenset[str]
    #: Model methods that mutate a settlement field, so calling one from outside
    #: the class is a settlement write.
    mutating_methods: frozenset[str]
    #: Settlement fields a ``create()`` can set to a value that makes a line
    #: settled at birth: those the model gives a default (or lets be null), so
    #: passing one is the caller overriding "born outstanding". A required field
    #: like ``quantity_ordered`` is not one of these — a line created with only a
    #: quantity ordered is NOT_RECEIVED by construction.
    create_settling_fields: frozenset[str]
    #: ``(path, first_line, last_line)`` spans of the class that owns the fields
    #: and of the queryset its manager is built from. Code inside them may read
    #: the fields raw; that is what they are for.
    exempt_spans: tuple[tuple[str, int, int], ...]
    #: The related name a purchase order reaches its lines by, so ``.items``
    #: on a queryset is recognised as this model.
    related_name: str

    @property
    def all_fields(self) -> frozenset[str]:
        return frozenset(self.fields)


@dataclass
class Finding:
    path: str
    line: int
    arm: str  # "predicate" | "write"
    detail: str
    snippet: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.arm}] {self.detail}\n      {self.snippet}"


@dataclass
class Report:
    anchor: Anchor
    findings: list[Finding] = field(default_factory=list)
    #: Every site that names a settlement field, judgement or not — the derived
    #: set the PR reports, as ``(path, line, role, snippet)``.
    sites: list[tuple[str, int, str, str]] = field(default_factory=list)
    #: Trees this run actually read.
    scanned: list[str] = field(default_factory=list)
    #: Trees it could not, and why. Never silently empty: a run that saw less
    #: than the whole tree has to say so rather than read as a clean sweep.
    unscanned: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Phase A — derive the anchor from the model
# --------------------------------------------------------------------------


def _field_decl_name(value: ast.expr) -> str | None:
    """The Django field class a class-body assignment declares, if it declares one."""
    if not isinstance(value, ast.Call):
        return None
    func = value.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name and (
        name.endswith("Field") or name in ("ForeignKey", "OneToOneField", "ManyToManyField")
    ):
        return name
    return None


def _has_kwarg(call: ast.Call, *names: str) -> bool:
    return any(kw.arg in names for kw in call.keywords)


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _truth_positions(node: ast.AST):
    """The sub-expressions ``node`` evaluates for truth rather than for value."""
    if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
        yield node.test
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield node.operand
    elif isinstance(node, ast.BoolOp):
        yield from node.values
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        for gen in node.generators:
            yield from gen.ifs


def derive_anchor(models_path: Path, rel_models_path: str) -> Anchor:
    """Read the settlement definition off ``PurchaseOrderItem`` itself."""
    tree = ast.parse(models_path.read_text())
    cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "PurchaseOrderItem"
    )

    fields: dict[str, str] = {}
    field_decls: dict[str, ast.Call] = {}
    members: dict[str, ast.AST] = {}
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name):
                decl = _field_decl_name(stmt.value)
                if decl is not None:
                    fields[target.id] = decl
                    field_decls[target.id] = stmt.value
                else:
                    members.setdefault(target.id, stmt)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            members.setdefault(stmt.name, stmt)

    # Transitive closure from the seed: follow every ``self.X`` the definition
    # reads. A name that is a field terminates the walk; a name that is another
    # member continues it.
    reached_members: set[str] = set()
    reached_fields: set[str] = set()
    stack = [SEED]
    while stack:
        name = stack.pop()
        if name in reached_members:
            continue
        reached_members.add(name)
        node = members.get(name)
        if node is None:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                if sub.value.id not in ("self", "cls"):
                    continue
                if sub.attr in fields:
                    reached_fields.add(sub.attr)
                elif sub.attr in members:
                    stack.append(sub.attr)

    # Quantity vs marker, from the declared column type. A quantity is a number
    # that only means something next to another number; a marker records that
    # something happened.
    quantities = frozenset(f for f in reached_fields if "IntegerField" in fields[f])
    markers = frozenset(reached_fields) - quantities

    # Which markers does the definition trust on their own? A field it reads as
    # a bare truth test (``if self.is_voided``) answers its own question, so
    # asking it elsewhere is fair. One it only ever compares against another
    # settlement field answers nothing alone — reading it outside the class is a
    # wrong answer whatever it is called.
    trusted_alone: set[str] = set()
    for name in reached_members:
        node = members.get(name)
        if node is None:
            continue
        for sub in ast.walk(node):
            for test in _truth_positions(sub):
                if (
                    isinstance(test, ast.Attribute)
                    and isinstance(test.value, ast.Name)
                    and test.value.id in ("self", "cls")
                    and test.attr in markers
                ):
                    trusted_alone.add(test.attr)
    entangled = markers - trusted_alone

    mutating = {
        stmt.name
        for stmt in cls.body
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(sub, (ast.Assign, ast.AugAssign))
            and any(
                isinstance(t, ast.Attribute) and t.attr in reached_fields
                for t in (sub.targets if isinstance(sub, ast.Assign) else [sub.target])
            )
            for sub in ast.walk(stmt)
        )
    }

    create_settling = frozenset(
        f
        for f in reached_fields
        if _has_kwarg(field_decls[f], "default") or _has_kwarg(field_decls[f], "null")
    )

    # The manager's queryset class is part of the model's own derivation — it is
    # where the ORM twin of ``receipt_state`` has to live — so it is exempt too.
    # Found through the class body rather than named here, so renaming it or
    # dropping it needs no edit to this file.
    spans = [(rel_models_path, cls.lineno, cls.end_lineno or cls.lineno)]
    manager_source = members.get("objects")
    if isinstance(manager_source, ast.Assign):
        for sub in ast.walk(manager_source.value):
            if isinstance(sub, ast.Name):
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == sub.id:
                        spans.append((rel_models_path, node.lineno, node.end_lineno or node.lineno))

    related = "items"
    fk = field_decls.get("purchase_order")
    if fk is not None:
        value = _kwarg(fk, "related_name")
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            related = value.value

    return Anchor(
        fields={f: fields[f] for f in sorted(reached_fields)},
        quantities=quantities,
        markers=markers,
        entangled=entangled,
        members=frozenset(reached_members),
        mutating_methods=frozenset(mutating),
        create_settling_fields=create_settling,
        exempt_spans=tuple(spans),
        related_name=related,
    )


# --------------------------------------------------------------------------
# Phase B — sweep for sites
# --------------------------------------------------------------------------


class _PyScanner:
    """Find settlement predicates and settlement writes in one Python module."""

    def __init__(self, anchor: Anchor, rel: str, source: str):
        self.a = anchor
        self.rel = rel
        self.lines = source.splitlines()
        self.tree = ast.parse(source)
        self.lookup_re = re.compile(r"^(%s)(__.+)?$" % "|".join(sorted(anchor.all_fields)))
        self.findings: list[Finding] = []
        self.sites: list[tuple[str, int, str, str]] = []
        #: function qualname -> {"writes": bool, "refreshes": bool, "calls": set,
        #: "line": int}
        self.functions: dict[str, dict] = {}

    # -- helpers ---------------------------------------------------------

    def _exempt(self, node: ast.AST) -> bool:
        line = getattr(node, "lineno", 0)
        return any(self.rel == path and lo <= line <= hi for path, lo, hi in self.a.exempt_spans)

    def _snippet(self, line: int) -> str:
        return self.lines[line - 1].strip()[:130] if 0 < line <= len(self.lines) else ""

    def _lookup_field(self, text: str) -> str | None:
        m = self.lookup_re.match(text)
        return m.group(1) if m else None

    def _fields_in(self, nodes) -> set[str]:
        """Settlement fields named anywhere in ``nodes`` — attribute, bare name,
        ORM keyword, or ORM lookup string. A site cannot avoid naming them."""
        found: set[str] = set()
        for node in nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr in self.a.all_fields:
                    found.add(sub.attr)
                elif isinstance(sub, ast.Name) and sub.id in self.a.all_fields:
                    found.add(sub.id)
                elif isinstance(sub, ast.keyword) and sub.arg:
                    hit = self._lookup_field(sub.arg)
                    if hit:
                        found.add(hit)
                elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    hit = self._lookup_field(sub.value)
                    if hit:
                        found.add(hit)
        return found

    def _targets_lines(self, call: ast.Call) -> bool:
        """Whether a ``create``/``update`` call writes purchase-order LINES.

        ``DeliveryItem`` and ``LeadTimeLog`` carry a ``quantity_received`` of
        their own; recording a receipt against one of those is not a settlement
        write and must not be asked to refresh anything. Resolved off the
        receiver — ``PurchaseOrderItem.objects`` or the order's own related
        manager, whose name comes from the model's own FK declaration.
        """
        receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
        if receiver is None:
            return False
        for sub in ast.walk(receiver):
            if isinstance(sub, ast.Name) and sub.id == "PurchaseOrderItem":
                return True
            if isinstance(sub, ast.Attribute) and sub.attr == self.a.related_name:
                return True
        return False

    def _flag(self, node: ast.AST, detail: str) -> None:
        line = getattr(node, "lineno", 0)
        self.findings.append(
            Finding(self.rel, line, "predicate", detail, self._snippet(line)),
        )

    def _judge(self, node: ast.AST, nodes, context: str) -> None:
        """Apply the one rule to an expression: two fields together, or a marker."""
        if self._exempt(node):
            return
        found = self._fields_in(nodes)
        if not found:
            return
        entangled = found & self.a.entangled
        if entangled:
            self._flag(
                node,
                f"{context} reads {'/'.join(sorted(entangled))} on its own — the definition "
                f"never trusts that field alone, so this answer is already wrong",
            )
        elif len(found) >= 2:
            self._flag(
                node,
                f"{context} brings {' and '.join(sorted(found))} together — that is "
                f"a re-implementation of the settlement predicate",
            )

    # -- arms ------------------------------------------------------------

    def _scan_predicates(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Compare):
                # ``x is None`` on a quantity is a null guard, not a judgement:
                # it asks whether the value exists, not what it means.
                is_null_guard = all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops) and all(
                    isinstance(c, ast.Constant) and c.value is None for c in node.comparators
                )
                if is_null_guard and not (self._fields_in([node]) & self.a.entangled):
                    continue
                self._judge(node, [node], "comparison")
            elif isinstance(node, ast.BinOp):
                if self._exempt(node):
                    continue
                found = self._fields_in([node])
                if len(found) >= 2:
                    self._flag(
                        node,
                        f"arithmetic brings {' and '.join(sorted(found))} together — that "
                        f"is a re-implementation of the settlement predicate",
                    )
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in PREDICATE_CALLS:
                    # Arguments only: the receiver chain belongs to its own call.
                    self._judge(node, list(node.args) + list(node.keywords), f"{name}()")
            elif isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                self._judge(node.test, [node.test], "truth test")
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                self._judge(node, [node.operand], "truth test")
            elif isinstance(node, ast.BoolOp):
                for value in node.values:
                    self._judge(value, [value], "truth test")
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                for gen in node.generators:
                    for test in gen.ifs:
                        self._judge(test, [test], "truth test")

    def _scan_functions(self) -> None:
        """Record, per function, whether it writes settlement state and whether it
        re-derives the order's status."""
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if self._exempt(node):
                continue
            qual = f"{self.rel}:{node.name}"
            writes: list[str] = []
            refreshes = False
            calls: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Assign, ast.AugAssign)):
                    targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                    for target in targets:
                        if isinstance(target, ast.Attribute) and target.attr in self.a.all_fields:
                            writes.append(f"{target.attr} (assignment)")
                elif isinstance(sub, ast.Call):
                    func = sub.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    calls.add(name)
                    if name == REFRESH:
                        refreshes = True
                    if name in WRITE_CALLS and self._targets_lines(sub):
                        settling = (
                            self.a.create_settling_fields
                            if name in ("create", "bulk_create")
                            else self.a.all_fields
                        )
                        for kw in sub.keywords:
                            if kw.arg in settling:
                                writes.append(f"{kw.arg} ({name}())")
            self.functions[qual] = {
                "writes": writes,
                "refreshes": refreshes,
                "calls": calls,
                "name": node.name,
                "line": node.lineno,
            }

    def _record_sites(self) -> None:
        """Every mention of a settlement field, so the derived set can be reported
        in full rather than only where it went wrong."""
        for node in ast.walk(self.tree):
            found: set[str] = set()
            if isinstance(node, ast.Attribute) and node.attr in self.a.all_fields:
                found = {node.attr}
            elif isinstance(node, ast.keyword) and node.arg:
                hit = self._lookup_field(node.arg)
                found = {hit} if hit else set()
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                hit = self._lookup_field(node.value)
                found = {hit} if hit else set()
            if found:
                line = getattr(node, "lineno", 0)
                self.sites.append(
                    (self.rel, line, ",".join(sorted(found)), self._snippet(line)),
                )

    def run(self) -> None:
        self._scan_predicates()
        self._scan_functions()
        self._record_sites()


# --- frontend --------------------------------------------------------------

_TS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_TS_LINE_COMMENT = re.compile(r"//.*")


def _ts_units(line: str) -> list[str]:
    """Split a TS line into expression units.

    Template-literal interpolations are separate expressions, so
    ``${line.quantity_received}/${line.quantity_ordered} received`` is two units
    naming one field each — a display, not a judgement — while
    ``a.quantity_ordered - a.quantity_received`` stays one unit naming two.
    """
    return [part for part in re.split(r"\$\{|\}|`", line) if part.strip()]


_TS_DECL = re.compile(r"^\s*(?:readonly\s+)?[A-Za-z_][\w]*\??\s*:")


def _scan_ts(anchor: Anchor, rel: str, source: str) -> tuple[list[Finding], list[tuple]]:
    """The frontend arm.

    Line-based, because there is no TypeScript parser in the standard library —
    weaker than the Python arm, and said so plainly in the report rather than
    left to be assumed equivalent.
    """
    findings: list[Finding] = []
    sites: list[tuple] = []
    source = _TS_BLOCK_COMMENT.sub("", source)
    word = re.compile(r"\b(%s)\b" % "|".join(sorted(anchor.all_fields)))
    for number, raw in enumerate(source.splitlines(), start=1):
        line = _TS_LINE_COMMENT.sub("", raw)
        if not word.search(line):
            continue
        sites.append((rel, number, ",".join(sorted(set(word.findall(line)))), raw.strip()[:130]))
        # ``quantity_received: number;`` and ``{ quantity_received: qty }`` name
        # the API's shape; they do not judge it.
        if _TS_DECL.match(line):
            continue
        for unit in _ts_units(line):
            found = set(word.findall(unit))
            entangled = found & anchor.entangled
            if entangled:
                findings.append(
                    Finding(
                        rel,
                        number,
                        "predicate",
                        f"reads {'/'.join(sorted(entangled))} on its own client-side — the "
                        f"definition never trusts that field alone; the API already sends "
                        f"the derived receipt_state / is_settled",
                        raw.strip()[:130],
                    )
                )
                break
            if len(found) >= 2:
                findings.append(
                    Finding(
                        rel,
                        number,
                        "predicate",
                        f"brings {' and '.join(sorted(found))} together client-side — that is "
                        f"a re-implementation of the settlement predicate",
                        raw.strip()[:130],
                    )
                )
                break
    return findings, sites


# --------------------------------------------------------------------------


def _roots(start: Path | None = None) -> tuple[Path, Path, Path | None]:
    """``(base, backend, frontend_or_None)``, anchored on this module's own home.

    Deliberately NOT "walk up until you see backend/ and frontend/": the
    docker-compose CI job mounts ``./backend`` alone at ``/app`` and has no
    frontend tree at all, and a search for both would simply crash there. The
    backend root is the directory this module's package lives in, whatever that
    directory is called, and the frontend is looked for beside it.
    """
    backend = (start or Path(__file__).resolve()).parents[1]
    frontend = backend.parent / "frontend" / "src"
    return backend.parent, backend, frontend if frontend.is_dir() else None


def _walk(root: Path, *suffixes: str):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in _SKIP_DIR_PARTS for part in path.parts):
            continue
        yield path


def scan(start: Path | None = None) -> Report:
    """Derive the anchor, then report every settlement site the tree exposes."""
    base, backend, frontend = _roots(start)

    def rel_to_base(path: Path) -> str:
        return path.relative_to(base).as_posix()

    models_path = backend / "reorder_queue" / "models.py"
    anchor = derive_anchor(models_path, rel_to_base(models_path))
    report = Report(anchor=anchor, scanned=[rel_to_base(backend)])

    functions: dict[str, dict] = {}
    for path in _walk(backend, ".py"):
        rel = rel_to_base(path)
        try:
            source = path.read_text()
            scanner = _PyScanner(anchor, rel, source)
            scanner.run()
        except (SyntaxError, UnicodeDecodeError):
            continue
        report.sites.extend(scanner.sites)
        if _is_test_path(rel):
            continue
        report.findings.extend(scanner.findings)
        functions.update(scanner.functions)

    if frontend is None:
        # "Not looked at" and "looked at and clean" are different facts, and the
        # report has to be able to tell them apart — the frontend arm is covered
        # by the Frontend Lint job on a full checkout, but a run that could not
        # see the tree must not read as one that cleared it.
        report.unscanned.append("frontend/src (not present in this checkout)")
    else:
        report.scanned.append(rel_to_base(frontend))
        for path in _walk(frontend, ".ts", ".tsx"):
            rel = rel_to_base(path)
            findings, sites = _scan_ts(anchor, rel, path.read_text())
            report.sites.extend(sites)
            if not _is_test_path(rel):
                report.findings.extend(findings)

    report.findings.extend(_write_arm(anchor, functions))
    report.findings.sort(key=lambda f: (f.path, f.line))
    return report


def _write_arm(anchor: Anchor, functions: dict[str, dict]) -> list[Finding]:
    """Every path that can settle a line must re-derive the order's status.

    Writing a settlement field is not a thing a caller can be trusted to
    remember to follow with a status refresh — that is precisely what
    ``update_item`` did not do, and what left orders stranded at
    ``partially_received`` with nothing outstanding and both close-out actions
    refusing them. So the obligation is not attached to the line of code that
    writes: it is attached to the write and then travels UP, and is discharged
    by a function that calls :data:`REFRESH`.

    A writer is therefore satisfied when it refreshes itself, or when every
    caller of it is satisfied — which is what lets a helper like
    ``apply_line_quantity`` stay a pure mutator while the transaction boundary
    above it owns the refresh. A writer with no caller anywhere in the tree is
    NOT satisfied: an unreachable obligation is still an obligation, and saying
    "nothing calls it" is a different fact from "it re-derives".

    Calling one of the model's own mutating methods (``close_short``,
    ``reopen_short``) counts as writing, because from outside the class that is
    exactly what it is.
    """
    by_name: dict[str, list[str]] = {}
    callers: dict[str, set[str]] = {qual: set() for qual in functions}
    for qual, info in functions.items():
        by_name.setdefault(info["name"], []).append(qual)
    for qual, info in functions.items():
        for called in info["calls"]:
            for target in by_name.get(called, ()):
                if target != qual:
                    callers[target].add(qual)

    # Reaching the refresh through a helper still reaches it. Without this,
    # extracting the call into a one-line function would defeat the arm, which
    # would make the arm a rule about code shape rather than about behaviour.
    reaches_refresh = {qual for qual, info in functions.items() if info["refreshes"]}
    changed = True
    while changed:
        changed = False
        for qual, info in functions.items():
            if qual in reaches_refresh:
                continue
            for called in info["calls"]:
                if any(target in reaches_refresh for target in by_name.get(called, ())):
                    reaches_refresh.add(qual)
                    changed = True
                    break

    obligations: dict[str, str] = {}
    for qual, info in functions.items():
        if info["writes"]:
            obligations[qual] = ", ".join(sorted(set(info["writes"])))
        for method in sorted(anchor.mutating_methods):
            if method in info["calls"]:
                obligations.setdefault(qual, f"{method}() on the line")

    def satisfied(qual: str, seen: frozenset[str]) -> bool:
        if qual in reaches_refresh:
            return True
        if qual in seen:  # a cycle discharges nothing
            return False
        upstream = callers[qual]
        if not upstream:
            return False
        return all(satisfied(parent, seen | {qual}) for parent in upstream)

    findings = []
    for qual, why in sorted(obligations.items()):
        if satisfied(qual, frozenset()):
            continue
        path, name = qual.split(":", 1)
        findings.append(
            Finding(
                path,
                functions[qual]["line"],
                "write",
                f"{name}() can change whether a line is settled ({why}), and neither it nor "
                f"every path into it calls {REFRESH}() — so the order is left with a status "
                f"claiming something its own lines no longer say",
                "",
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    report = scan()
    anchor = report.anchor

    print("Settlement definition, derived from PurchaseOrderItem.%s:" % SEED)
    for name, decl in anchor.fields.items():
        if name in anchor.entangled:
            kind = "marker (never trusted alone)"
        elif name in anchor.markers:
            kind = "marker"
        else:
            kind = "quantity"
        print(f"  {name:<20} {decl:<22} {kind}")
    print("  derivation members: " + ", ".join(sorted(anchor.members)))
    print("  mutating methods:   " + ", ".join(sorted(anchor.mutating_methods)))
    print()
    print("Scanned: " + ", ".join(report.scanned))
    for missing in report.unscanned:
        print(f"NOT scanned: {missing}")
    print()

    if "--sites" in argv:
        print(f"All {len(report.sites)} sites naming a settlement field:")
        for path, line, names, snippet in report.sites:
            print(f"  {path}:{line}  {names}\n      {snippet}")
        print()

    if not report.findings:
        print("No site bypasses the derivation.")
        return 0

    print(f"{len(report.findings)} site(s) bypass the settlement derivation:\n")
    for finding in report.findings:
        print(finding)
        print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
