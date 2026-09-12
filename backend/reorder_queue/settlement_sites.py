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

The write arm has been wrong about its own reach repeatedly, and the record of
that is kept as DATA in :data:`WRITE_ARM_SURPRISES` rather than counted out in
prose here. It is kept because each time, the previous description implied a
completeness it did not have — and because the count itself went the same way:
several places in this repository each stated how much the arm could not see,
by hand, and no two of those numbers agreed with each other or with the length
of any list in the report. Every number about these lists is now ``len()`` of
the list it describes, so there is nothing left to keep in step.

The admin surprises are no longer this file's problem: model-level writes are
routed by :mod:`reorder_queue.settlement_signals`, on the line's own save and
delete signals, so the admin arm that used to enumerate ``save_model`` /
``save_formset`` / ``delete_model`` / ``delete_queryset`` has been RETIRED
rather than extended. The caller-graph ones are fixed at the graph — see
``resolve`` in :func:`_write_arm`, where a call resolves only where the
RECEIVER names the thing being called.

The FAST DELETE cannot be closed here at all — a signal nobody sends is
invisible to a scanner and to a receiver alike — so it is NAMED instead, in
:data:`WRITE_SHAPES_UNSEEN` and in what :func:`main` prints.

What that leaves this arm is the writes NO SIGNAL FIRES FOR: queryset-level
``update()`` (``void_po`` is the live example) and fast deletes, plus the
explicit service boundaries. Read the split as the boundary, not as a footnote.

So this file does not claim to see every write, and must not be read as if it
did. :data:`WRITE_SHAPES_SEEN` and :data:`WRITE_SHAPES_UNSEEN` enumerate both
halves, and :func:`main` PRINTS them on every run beside the derived definition
and the trees it could read, so the edges travel with the report rather than
living in a docstring nobody opens. A derivation that implies a completeness it
does not have is worse than one that names its edges.

Run it directly for a report::

    python3 backend/reorder_queue/settlement_sites.py

Exits non-zero when a site bypasses the derivation, and equally when a file in a
tree it swept could not be read at all — an unparseable module is a site nobody
judged, and reporting one as clean is the failure this whole derivation exists to
prevent. Both are what ``reorder_queue/tests/test_settlement_sites.py`` asserts on
and what CI runs. Stdlib only, and it imports nothing from Django, so the
frontend-lint job can run it without a backend environment — but it must run it
under an interpreter that can parse the backend, or it reports on nothing and
says so.
"""

from __future__ import annotations

import ast
import os
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

#: The subset of :data:`PREDICATE_CALLS` whose arguments are INDEPENDENT of one
#: another rather than one conjoined condition. ``filter(a=..., b=...)`` relates
#: its keywords — they AND together into a single question — but
#: ``aggregate(x=Sum("a"), y=Sum("b"))`` does not: those are two separate
#: columns that happen to be asked for in one round-trip, and reporting two
#: gross totals side by side is not a re-implementation of anything. So each
#: argument of these is judged as its own expression, which still catches the
#: real thing (``update(quantity_received=F("quantity_ordered"))`` names two
#: settlement fields inside ONE keyword and is flagged).
INDEPENDENT_ARG_CALLS = frozenset({"aggregate", "annotate", "update"})

#: Call names that persist a field value passed as a keyword.
WRITE_CALLS = frozenset({"create", "update", "get_or_create", "update_or_create", "bulk_create"})

#: Every time the write arm turned out not to reach what its own description
#: claimed. Data, not prose: :func:`main` derives the count it reports from
#: ``len()`` of this, so the record cannot say one number while the list says
#: another — which is exactly what happened, in three places at once, and is
#: why this is a tuple.
#:
#: Two different failures are recorded together on purpose, because the arm was
#: equally wrong both ways: a write SHAPE it could not see, and a discharge it
#: granted without establishing it. The second is the worse one — a shape it
#: cannot see is at least a hole it can name.
WRITE_ARM_SURPRISES = (
    "SHAPE: it knew only attribute assignment and create()/update() keywords",
    "SHAPE: a Django ModelAdmin writes through a ModelForm, naming no settlement field",
    "SHAPE: a DELETE changes the answer while writing no field whatsoever",
    "DISCHARGE: its caller graph matched BARE function names, so an unrelated "
    "function of the same name elsewhere in the TREE could discharge a real "
    "writer's obligation — the guard certifying the very thing it was built to "
    "catch",
    "SHAPE: a FAST DELETE fires no post_delete at all, so the model-level "
    "routing that covers ordinary deletes does not cover those",
    "DISCHARGE: narrowed to the module, the caller graph still matched on the "
    "NAME alone — any receiver's method call resolved to a same-named "
    "definition in the module, so a method call on an unrelated object "
    "discharged a writer nothing called. The hole above, one scope tighter",
)

#: The write shapes this scan can actually see. Stated so the arm is never read
#: as exhaustive — see :data:`WRITE_ARM_SURPRISES` for what it has missed.
WRITE_SHAPES_SEEN = (
    "assignment to a settlement field on a line (obj.quantity_received = ...)",
    "create()/update()/get_or_create()/update_or_create()/bulk_create() with a "
    "settlement field as a keyword",
    "a call to one of the model's own mutating methods (close_short, reopen_short)",
    "an update() whose keywords name settlement fields, even on a receiver this "
    "scan cannot resolve — a false positive there costs one explicit receiver",
    "a model-level save or delete of a line, wherever it comes from — NOT by this "
    "scan, but by reorder_queue.settlement_signals, which is why the admin arm "
    "this file used to carry was retired rather than extended",
)

#: What it cannot see. These are holes, not absences of sites — "found nothing"
#: and "could not tell" are different facts and this list is which is which.
WRITE_SHAPES_UNSEEN = (
    "raw SQL, and anything reaching the database outside the ORM",
    "bulk_update(), and queryset writers not named above — querysets fire no "
    "per-object save signal either, so neither half of the routing sees them",
    "a FAST DELETE: a collector that can drop rows with one _raw_delete sends no "
    "post_delete, and _raw_delete called directly never does, so the model-level "
    "routing that covers ordinary deletes does not cover those",
    "a write through a serializer or form outside the paths named above",
    "settlement fields pulled into locals by values_list() and compared later",
    "arithmetic on order-level aggregate PROPERTIES rather than on the line fields "
    "— which is how the pending_orders site hid, found by reading not by this",
    "a call this scan cannot resolve to a definition: it buys no discharge, so the "
    "arm fails CLOSED there and may ask for a refresh a real caller already makes",
    "on the frontend, anything a line-based regex cannot see: there is no "
    "TypeScript parser in the standard library, so that arm is weaker than the "
    "AST-based Python one and must not be read as its equal",
)

#: Directory NAMES that never hold first-party source. Vendored dependencies and
#: build output, not code anyone here writes — judging them says nothing about
#: this repository and, because an unreadable file FAILS the run, a third-party
#: module the running interpreter cannot parse would fail it on someone else's
#: syntax.
_SKIP_DIR_PARTS = frozenset(
    {
        "__pycache__",
        "node_modules",
        "site-packages",
        "staticfiles",
        "media",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".eggs",
    }
)

#: What marks a directory as a Python ENVIRONMENT rather than source: the file
#: :mod:`venv` writes at the root of every one it creates. Derived rather than
#: listed on purpose — ``.venv`` was the only name the list carried, and a
#: developer whose environment is ``venv/``, ``env/``, ``.venv-3.11/`` or
#: anything else had the whole of ``site-packages`` swept as if it were this
#: repository's code. Nothing here has to guess the name.
_VENV_MARKER = "pyvenv.cfg"


def _skip_dir(path: Path) -> bool:
    """Whether the sweep should not descend into ``path``."""
    return path.name in _SKIP_DIR_PARTS or (path / _VENV_MARKER).is_file()


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

    #: The model class the definition lives on. Carried so arms that reason
    #: about a model rather than about an expression — the admin arm — can name
    #: it without a second hand-written copy.
    model_name: str
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
    #: Files that WERE there and could not be read, as ``(path, reason)``: a
    #: decode failure, or source this interpreter cannot parse.
    #:
    #: Deliberately separate from :attr:`unscanned`. A tree missing from the
    #: checkout is a known shape of run — the docker-compose job mounts
    #: ``backend/`` alone — and the report names it and carries on. An
    #: unreadable file is not that: it is a hole INSIDE a tree this run has
    #: already claimed, in :attr:`scanned`, to have swept. Every other file in
    #: that tree was judged; this one was skipped, and skipping is not clearing.
    #: So it FAILS the run. "Found nothing" and "could not tell" are different
    #: facts, and a guard that renders a clean verdict without reading the
    #: evidence is worse than one that errors, because it is believed.
    unreadable: list[tuple[str, str]] = field(default_factory=list)

    @property
    def swept_whole_tree(self) -> bool:
        """Whether this run actually read everything it set out to read."""
        return not self.unscanned and not self.unreadable


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
    tree = ast.parse(models_path.read_text(encoding="utf-8"))
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
        model_name=cls.name,
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


def _receiver_path(func: ast.expr) -> tuple[str, ...] | None:
    """What a call was made ON, as the chain of plain names it reduces to.

    ``None`` for a bare ``name(...)``; ``("services",)`` for
    ``services.apply_line_quantity(...)``; ``("self",)`` for
    ``self.close_short()``; ``("mod", "helpers")`` for
    ``mod.helpers.apply(...)``; and the EMPTY tuple when the chain does not
    reduce to plain names at all (``get_queryset().close_short()``,
    ``self.lines[0].close_short()``) — a receiver no syntax can identify.

    The whole soundness of the caller graph rests here. A receiver is what
    decides which definition a call actually reaches: ``self.f()`` reaches this
    class's ``f``, ``mod.f()`` reaches the imported module's, and
    ``some_local.f()`` reaches something this scan cannot name. Keeping the
    FULL chain rather than only its root is what stops ``Outer.Inner.f()``
    being read as ``Outer.f()``.
    """
    if not isinstance(func, ast.Attribute):
        return None
    parts: list[str] = []
    node = func.value
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ()
    parts.append(node.id)
    return tuple(reversed(parts))


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
        #: Dotted names of every class this module declares, so a receiver that
        #: names one — ``PurchaseOrderItem.close_short()`` — can be told apart
        #: from a receiver that merely holds an object.
        self.classes: frozenset[str] = self._class_dotted()
        self.imports, self.shadows, self.definitions = self._scope_bindings()
        #: function qualname -> {"writes": bool, "refreshes": bool, "calls": set,
        #: "line": int}
        self.functions: dict[str, dict] = {}

    # -- helpers ---------------------------------------------------------

    def _import_binding(self, node: ast.Import | ast.ImportFrom, alias: ast.alias):
        module_parts = Path(self.rel).with_suffix("").parts
        if module_parts and module_parts[0] == "backend":
            module_parts = module_parts[1:]
        package = list(module_parts[:-1])
        if isinstance(node, ast.Import):
            binding = alias.asname or alias.name.split(".")[0]
            module = alias.name if alias.asname else alias.name.split(".")[0]
            return binding, (module, None)
        parent = package[: len(package) - max(node.level - 1, 0)] if node.level else []
        module = ".".join(parent + ((node.module or "").split(".") if node.module else []))
        return alias.asname or alias.name, (module, alias.name)

    @staticmethod
    def _scope_nodes(scope: ast.AST):
        stack = list(scope.body) if isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)) else []
        while stack:
            node = stack.pop()
            yield node
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.extend(node.decorator_list)
                stack.extend(node.args.defaults)
                stack.extend(default for default in node.args.kw_defaults if default is not None)
                continue
            if isinstance(node, ast.ClassDef):
                stack.extend(node.decorator_list)
                stack.extend(node.bases)
                stack.extend(keyword.value for keyword in node.keywords)
                continue
            if isinstance(node, ast.Lambda):
                stack.extend(node.args.defaults)
                stack.extend(default for default in node.args.kw_defaults if default is not None)
                continue
            stack.extend(ast.iter_child_nodes(node))

    @staticmethod
    def _target_names(target: ast.AST) -> set[str]:
        return {node.id for node in ast.walk(target) if isinstance(node, ast.Name)}

    def _bindings_in(self, scope: ast.AST):
        imports: dict[str, set[tuple[str, str | None]]] = {}
        shadows: set[str] = set()
        definitions: set[str] = set()
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = scope.args
            shadows.update(arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs))
            if args.vararg:
                shadows.add(args.vararg.arg)
            if args.kwarg:
                shadows.add(args.kwarg.arg)
        for node in self._scope_nodes(scope):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name, binding = self._import_binding(node, alias)
                    imports.setdefault(name, set()).add(binding)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                shadows.add(node.name)
                definitions.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    shadows.update(self._target_names(target))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                shadows.update(self._target_names(node.target))
            elif isinstance(node, ast.withitem) and node.optional_vars:
                shadows.update(self._target_names(node.optional_vars))
            elif isinstance(node, ast.ExceptHandler) and node.name:
                shadows.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                shadows.update(node.names)
        return imports, shadows, definitions

    def _scope_bindings(self):
        imports: dict[str, dict[str, set[tuple[str, str | None]]]] = {}
        shadows: dict[str, set[str]] = {}
        definitions: dict[str, set[str]] = {}
        imports[""], shadows[""], definitions[""] = self._bindings_in(self.tree)
        for dotted, node in self._qualified_functions(self.tree):
            imports[dotted], shadows[dotted], definitions[dotted] = self._bindings_in(node)
        return imports, shadows, definitions

    def _visible_imports(self, dotted: str):
        visible: dict[str, tuple[str, str | None] | None] = {}
        scopes = self._scopes(dotted)
        names = set().union(*(set(self.imports[scope]) | self.shadows[scope] for scope in scopes))
        for name in names:
            candidates = set().union(*(self.imports[scope].get(name, set()) for scope in scopes))
            shadowed = any(name in self.shadows[scope] for scope in scopes)
            visible[name] = next(iter(candidates)) if len(candidates) == 1 and not shadowed else None
        return visible

    def _class_dotted(self) -> frozenset[str]:
        """Every class in the module, named the way functions in it are named.

        Same qualification scheme as :meth:`_qualified_functions`, so a
        function's dotted name can be split against this set to say which of
        its enclosing scopes are CLASS bodies — which is what decides whether a
        bare name inside it can see a sibling at all.
        """
        found: set[str] = set()

        def walk(node: ast.AST, prefix: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    found.add(f"{prefix}{child.name}")
                    walk(child, f"{prefix}{child.name}.")
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, f"{prefix}{child.name}.")
                else:
                    walk(child, prefix)

        walk(self.tree, "")
        return frozenset(found)

    def _scopes(self, dotted: str) -> tuple[str, ...]:
        """The scopes a BARE name inside ``dotted`` could resolve in, inner first.

        Its own body, then each enclosing FUNCTION, then the module (``""``).
        Class bodies are deliberately absent: a method calling ``helper()``
        does not reach a sibling method of that name, it reaches a module-level
        function, and pretending otherwise invents a call edge.
        """
        parts = dotted.split(".")
        scopes = [dotted]
        for depth in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:depth])
            if prefix not in self.classes:
                scopes.append(prefix)
        scopes.append("")
        return tuple(scopes)

    def _owner_class(self, dotted: str) -> str | None:
        """The class ``dotted`` is a method of, if it is one — for ``self.f()``."""
        parent, _, _ = dotted.rpartition(".")
        return parent if parent and parent in self.classes else None

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
            for sub in self._scope_nodes(node):
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

        A receiver naming NO model at all — ``qs.update(...)`` on a queryset
        held in a local, a helper's return value — is treated as lines. The
        resolution is syntactic and cannot follow a variable, and the two ways
        of being wrong are not symmetric: a false positive costs whoever wrote
        it one explicit receiver, while a false negative is the entire defect
        class this file exists to end. Only an identifier that names something
        else buys the call its way out.
        """
        receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
        if receiver is None:
            return False
        named: set[str] = set()
        for sub in ast.walk(receiver):
            if isinstance(sub, ast.Name):
                if sub.id == self.a.model_name:
                    return True
                named.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                if sub.attr == self.a.related_name:
                    return True
                named.add(sub.attr)
        return not any(name[:1].isupper() for name in named)

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
                    arguments = list(node.args) + list(node.keywords)
                    if name in INDEPENDENT_ARG_CALLS:
                        for argument in arguments:
                            self._judge(node, [argument], f"{name}()")
                    else:
                        self._judge(node, arguments, f"{name}()")
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

    def _qualified_functions(self, node: ast.AST, prefix: str = ""):
        """Every function in the module, named by its enclosing class.

        Qualified rather than bare because two classes in one module routinely
        define hooks of the same name — ``save_model`` on one admin and
        ``save_model`` on another — and a bare name would let one silently
        stand in for the other's obligation.
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from self._qualified_functions(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield f"{prefix}{child.name}", child
                yield from self._qualified_functions(child, f"{prefix}{child.name}.")
            else:
                yield from self._qualified_functions(child, prefix)

    def _scan_functions(self) -> None:
        """Record, per function, whether it writes settlement state and whether it
        re-derives the order's status."""
        for dotted, node in self._qualified_functions(self.tree):
            if self._exempt(node):
                continue
            qual = f"{self.rel}:{dotted}"
            writes: list[str] = []
            refreshes = False
            calls: set[str] = set()
            for sub in self._scope_nodes(node):
                if isinstance(sub, (ast.Assign, ast.AugAssign)):
                    targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                    for target in targets:
                        if isinstance(target, ast.Attribute) and target.attr in self.a.all_fields:
                            writes.append(f"{target.attr} (assignment)")
                elif isinstance(sub, ast.Call):
                    func = sub.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    calls.add((name, _receiver_path(func)))
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
                "module": self.rel,
                "imported": self._visible_imports(dotted),
                "shadowed": set().union(
                    *(
                        self.shadows[scope] - self.definitions[scope]
                        for scope in self._scopes(dotted)
                    )
                ),
                "classes": self.classes,
                "scopes": self._scopes(dotted),
                "owner_class": self._owner_class(dotted),
                "dotted": dotted,
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


#: A PROPERTY KEY: a name in the position where TypeScript declares or supplies
#: a member — the start of a line, or just after ``{``, ``,``, ``;`` or ``(`` —
#: followed by a colon. Anchored on that delimiter so a ternary's
#: ``cond ? a : b`` is not mistaken for one.
_TS_PROPERTY_KEY = re.compile(
    r"(^|[{,;(])(\s*(?:readonly\s+)?(?:[A-Za-z_$][\w$]*|(['\"])[^'\"]*\3)\s*\??\s*:)"
)


def _ts_without_keys(unit: str) -> str:
    """``unit`` with its property KEYS removed and their values left in place.

    ``quantity_received: number;`` and ``{ quantity_received: qty }`` name the
    API's shape; they do not judge it, so the key is dropped. What follows the
    colon is not the shape — it is an expression like any other, and this used
    to be thrown away with it.

    Skipping the whole LINE on the strength of it starting with a key is how
    ``ordered: line.quantity_ordered - line.quantity_received,`` — a variance
    re-derived by hand, inside an object literal — went unjudged, while a
    literal written along one line, ``{ quantity_received: a, quantity_ordered:
    b }``, was judged as an expression naming two fields and wrongly flagged.
    One rule replaces both: a key is a key wherever it sits, and a value is a
    value wherever it sits.
    """
    return _TS_PROPERTY_KEY.sub(r"\1 ", unit)


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
        for unit in _ts_units(line):
            found = set(word.findall(_ts_without_keys(unit)))
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


def _walk(root: Path, *suffixes: str) -> list[Path]:
    """Every source file under ``root``, skipped directories PRUNED rather than filtered.

    Pruning, not filtering, and only BELOW ``root``. The filter this replaces
    tested every component of the absolute path, so a checkout that merely
    happened to live under a directory called ``media`` — or under any
    virtualenv, which is where a ``pip install -e .`` checkout normally does
    live — matched on an ancestor nobody chose and yielded NOTHING, and a sweep
    of no files reports as a sweep that found no site.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not _skip_dir(here / name)]
        found.extend(here / name for name in filenames if Path(name).suffix in suffixes)
    return sorted(found)


def _why_unreadable(exc: Exception) -> str:
    """One line naming what stopped this file being read, for the report."""
    if isinstance(exc, SyntaxError):
        where = f", line {exc.lineno}" if exc.lineno else ""
        return (
            f"SyntaxError: {exc.msg}{where} — this interpreter "
            f"(Python {sys.version_info.major}.{sys.version_info.minor}) "
            f"cannot parse the file"
        )
    return f"{type(exc).__name__}: {exc}"


def scan(start: Path | None = None) -> Report:
    """Derive the anchor, then report every settlement site the tree exposes.

    A file the sweep could not read lands in :attr:`Report.unreadable` rather
    than being skipped, because the alternative is a report that says
    ``Scanned: backend`` over a backend it only partly read.
    """
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
            source = path.read_text(encoding="utf-8")
            scanner = _PyScanner(anchor, rel, source)
        except (SyntaxError, ValueError, OSError) as exc:
            # NOT a `continue`. This file is in `backend/`, which `report.scanned`
            # says was swept; passing over it in silence is how a run that judged
            # nothing reads as a run that judged everything and approved it.
            #
            # `ValueError` covers the decode failure (`UnicodeDecodeError` is a
            # subclass of it) and one more case, because the interpreter decides
            # which exception a bad file raises and the versions disagree: a NUL
            # byte in a module is a `SyntaxError` from `ast.parse` on 3.12+ and a
            # bare `ValueError` before it. This scan runs under whatever `python3`
            # the job has, so catching only what the newest one raises reproduces
            # the very blindness on older interpreters that this handler exists to
            # end — there as an unhandled crash rather than a silent pass.
            report.unreadable.append((rel, _why_unreadable(exc)))
            continue
        scanner.run()
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
            try:
                source = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                report.unreadable.append((rel, _why_unreadable(exc)))
                continue
            findings, sites = _scan_ts(anchor, rel, source)
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
    by_module_and_dotted: dict[tuple[str, str], list[str]] = {}
    for qual, info in functions.items():
        by_module_and_dotted.setdefault((info["module"], info["dotted"]), []).append(qual)

    modules = {info["module"] for info in functions.values()}

    def imported_module(dotted: str) -> str | None:
        path = dotted.replace(".", "/")
        suffixes = (f"/{path}.py", f"/{path}/__init__.py")
        hits = [module for module in modules if (f"/{module}").endswith(suffixes)]
        return hits[0] if len(hits) == 1 else None

    def resolve(caller: str, call: tuple[str, tuple[str, ...] | None]) -> list[str]:
        """The definitions a call could actually reach — never merely same-named.

        A name matched without regard to what the call was made ON is how the
        arm came to certify the thing it exists to catch. It happened twice, at
        two scopes. First across the tree: an uncalled writer named
        ``close_short`` was discharged by an unrelated module calling the
        MODEL's ``close_short()``. Then, once that was narrowed to the module,
        WITHIN one: any ``anything.close_short()`` in the module still resolved
        to the module's own ``close_short`` definition, so a method call on an
        object with nothing to do with it discharged the writer just the same.
        A name shared with the receiver's method is a coincidence of naming,
        and a guard may not spend a coincidence to discharge an obligation.

        So the RECEIVER decides, and a call resolves only where the receiver
        actually names the thing being called:

        1. a bare ``f(...)`` — the caller's own lexical scope chain, its
           enclosing functions and then the module, exactly as Python looks a
           name up; class bodies are not in that chain, so a method does not
           reach a sibling method this way;
        2. ``self.f(...)`` / ``cls.f(...)`` — the class the caller is a method
           of, and only that class;
        3. ``Cls.f(...)`` where ``Cls`` is a class this module DECLARES;
        4. a receiver whose root the module IMPORTS — the cross-module case
           round 4 left in place;
        5. anything else — a local, a parameter, a return value, a subscript —
           resolves to NOTHING, and the obligation stays where it is.

        Failing CLOSED at (5) can ask for a refresh a real caller already
        makes; failing open hands a settlement writer a clean bill of health.
        """
        name, receiver = call
        info = functions[caller]
        module = info["module"]

        def declared(dotted: str) -> list[str]:
            return [t for t in by_module_and_dotted.get((module, dotted), ()) if t != caller]

        def imported(
            binding: str, receiver_tail: tuple[str, ...], *, is_receiver: bool = False
        ) -> list[str]:
            import_info = info["imported"].get(binding)
            if import_info is None:
                return []
            base, symbol = import_info
            if is_receiver:
                parts = [base]
                if symbol is not None:
                    parts.append(symbol)
                parts.extend(receiver_tail)
                target_name = name
            else:
                if symbol is None:
                    return []
                parts = [base]
                target_name = symbol
            target_module = imported_module(".".join(part for part in parts if part))
            if target_module is None:
                return []
            return [
                target
                for target in by_module_and_dotted.get((target_module, target_name), ())
                if target != caller
            ]

        if receiver is None:
            if name in info["shadowed"]:
                return []
            for scope in info["scopes"]:
                hit = declared(f"{scope}.{name}" if scope else name)
                if hit:
                    return hit
            return imported(name, ())

        if not receiver:
            # The chain does not reduce to names at all; nothing to resolve.
            return []

        if receiver in (("self",), ("cls",)):
            owner = info["owner_class"]
            return declared(f"{owner}.{name}") if owner else []

        dotted_receiver = ".".join(receiver)
        if dotted_receiver in info["classes"]:
            return declared(f"{dotted_receiver}.{name}")

        return imported(receiver[0], receiver[1:], is_receiver=True)

    callers: dict[str, set[str]] = {qual: set() for qual in functions}
    for qual, info in functions.items():
        for call in info["calls"]:
            for target in resolve(qual, call):
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
            for call in info["calls"]:
                if any(target in reaches_refresh for target in resolve(qual, call)):
                    reaches_refresh.add(qual)
                    changed = True
                    break

    obligations: dict[str, str] = {}
    for qual, info in functions.items():
        called_names = {name for name, _receiver in info["calls"]}
        if info["writes"]:
            obligations[qual] = ", ".join(sorted(set(info["writes"])))
        for method in sorted(anchor.mutating_methods):
            if method in called_names:
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


def _surprise_tally() -> str:
    """``WRITE_ARM_SURPRISES`` broken down by kind, counted off the tuple itself."""
    counts: dict[str, int] = {}
    for surprise in WRITE_ARM_SURPRISES:
        kind, _, _ = surprise.partition(":")
        counts[kind] = counts.get(kind, 0) + 1
    return ", ".join(f"{count} {kind.lower()}" for kind, count in sorted(counts.items()))


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
    for path, reason in report.unreadable:
        print(f"NOT scanned: {path} ({reason})")
    print()

    # The edges travel with the report. A clean run means "none of the shapes
    # under the first heading bypassed the derivation", never "there is nothing
    # left". Every count below is len() of the list it introduces: this report
    # exists to state the guard's own limits honestly, and it once stated them
    # by hand, in numbers that disagreed with each other and with the lists.
    print(f"Write shapes this scan CAN see ({len(WRITE_SHAPES_SEEN)}):")
    for shape in WRITE_SHAPES_SEEN:
        print(f"  + {shape}")
    print(
        f"Write shapes it CANNOT see ({len(WRITE_SHAPES_UNSEEN)}) — "
        f"holes, not absences of sites:"
    )
    for shape in WRITE_SHAPES_UNSEEN:
        print(f"  - {shape}")
    print(
        f"The write arm has been wrong about its own reach "
        f"{len(WRITE_ARM_SURPRISES)} times ({_surprise_tally()}); "
        f"see WRITE_ARM_SURPRISES."
    )
    print()

    if "--sites" in argv:
        print(f"All {len(report.sites)} sites naming a settlement field:")
        for path, line, names, snippet in report.sites:
            print(f"  {path}:{line}  {names}\n      {snippet}")
        print()

    if report.unreadable:
        # Printed before the verdict, not after it, because it CHANGES the
        # verdict: none of these files was judged, so none of them was cleared.
        print(
            f"{len(report.unreadable)} file(s) in a tree above could not be read, "
            f"so they are NOT cleared:\n"
        )
        for path, reason in report.unreadable:
            print(f"  {path}\n      {reason}")
        print(
            "\nA guard that cannot read a file has not cleared it, so this run "
            "FAILS rather than reporting a sweep it did not perform. If these are "
            "SyntaxErrors, the interpreter running this scan is older than the one "
            "the backend targets: run it under that version."
        )
        print()

    if not report.findings:
        if report.swept_whole_tree:
            print("No site bypasses the derivation.")
        else:
            # The wording is load-bearing. The unqualified sentence is a claim
            # about the repository; this run only earned a claim about the part
            # of it that was read.
            print(
                "No site bypasses the derivation in what was read — this was NOT a "
                "whole-tree sweep, see the NOT scanned lines above."
            )
        return 1 if report.unreadable else 0

    print(f"{len(report.findings)} site(s) bypass the settlement derivation:\n")
    for finding in report.findings:
        print(finding)
        print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
