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

The write arm has now been blind to a write SHAPE three separate times. First it
knew only attribute assignment and ``create``/``update`` keywords. Then a Django
``ModelAdmin`` turned out to write through a ``ModelForm``, naming no settlement
field at all — derived from the admin CLASS instead: which model it edits, and
which of that model's settlement columns it leaves out of ``readonly_fields``.
Then a DELETE turned out to change the answer while writing no field whatsoever,
which no extension of a field-write rule could ever have caught.

So this file does not claim to see every write, and must not be read as if it
did. :data:`WRITE_SHAPES_SEEN` and :data:`WRITE_SHAPES_UNSEEN` enumerate both
halves, and :func:`main` PRINTS them on every run beside the derived definition
and the trees it could read, so the edges travel with the report rather than
living in a docstring nobody opens. A derivation that implies a completeness it
does not have is worse than one that names its edges.

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

#: Django's admin base classes. A subclass of one of these writes through a
#: ``ModelForm`` — never through an attribute assignment or a ``create()``
#: keyword — so the ordinary write arm cannot see it and the admin arm below
#: derives the obligation from the class instead.
ADMIN_BASES = frozenset({"ModelAdmin", "InlineModelAdmin", "TabularInline", "StackedInline"})

#: The hook a ``ModelAdmin``'s own change form writes its object through.
ADMIN_SAVE_HOOK = "save_model"

#: The hook an inline's rows are both written and DELETED through, on the parent.
ADMIN_FORMSET_HOOK = "save_formset"

#: Runs around the hooks above within one request, so a refresh here discharges
#: their door as surely as putting it in the door itself would. Deliberately not
#: a third interchangeable save hook: a ``ModelAdmin``'s own change form goes
#: through :data:`ADMIN_SAVE_HOOK` and an inline's rows through its parent's
#: :data:`ADMIN_FORMSET_HOOK`, and answering for one door in the other's hook
#: leaves the first one open.
ADMIN_SAVE_ALTERNATES = ("save_related",)

#: BOTH doors Django dispatches a delete to — a row delete reaches
#: ``delete_model`` and the "Delete selected" action reaches ``delete_queryset``,
#: and neither falls through to the other. Overriding one and leaving the other
#: at Django's default leaves that door open, so every one of these must reach
#: the refresh rather than any one of them.
#:
#: Separate from the save hooks because a delete is a different shape: it writes
#: no settlement field at all, so no extension of the field-write rule reaches
#: it, yet removing the last outstanding line changes the order's answer exactly
#: as editing that line would.
ADMIN_DELETE_HOOKS = ("delete_model", "delete_queryset")

#: The write shapes this scan can actually see. Stated so the arm is never read
#: as exhaustive — it has been surprised three times, and each surprise shipped.
WRITE_SHAPES_SEEN = (
    "assignment to a settlement field on a line (obj.quantity_received = ...)",
    "create()/update()/get_or_create()/update_or_create()/bulk_create() with a "
    "settlement field as a keyword",
    "a call to one of the model's own mutating methods (close_short, reopen_short)",
    "an update() whose keywords name settlement fields, even on a receiver this "
    "scan cannot resolve — a false positive there costs one explicit receiver",
    "a Django admin whose form leaves a settlement column editable (the ModelForm "
    "save names no field, so this is derived from the admin class)",
    "a Django admin that can delete lines (a delete writes no settlement field)",
)

#: What it cannot see. These are holes, not absences of sites — "found nothing"
#: and "could not tell" are different facts and this list is which is which.
WRITE_SHAPES_UNSEEN = (
    "raw SQL, and anything reaching the database outside the ORM",
    "bulk_update(), and queryset writers not named above",
    "a cascading delete from a parent row rather than from the line itself",
    "a write through a serializer, form or signal outside the Django admin",
    "settlement fields pulled into locals by values_list() and compared later",
    "arithmetic on order-level aggregate PROPERTIES rather than on the line fields "
    "— which is how the pending_orders site hid, found by reading not by this",
    "on the frontend, anything a line-based regex cannot see: there is no "
    "TypeScript parser in the standard library, so that arm is weaker than the "
    "AST-based Python one and must not be read as its equal",
)

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
        #: Admin classes that can write settlement state through a ModelForm.
        self.admin_obligations: list[dict] = []
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

    # -- admin arm -------------------------------------------------------

    def _class_assignments(self, cls: ast.ClassDef, *names: str):
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in names for target in stmt.targets
            ):
                yield stmt.value

    def _class_strings(self, cls: ast.ClassDef, *names: str) -> set[str]:
        """Every string constant assigned to one of ``names`` in the class body."""
        return {
            sub.value
            for value in self._class_assignments(cls, *names)
            for sub in ast.walk(value)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        }

    def _class_references(self, cls: ast.ClassDef, *names: str) -> set[str]:
        """Every bare name assigned to one of ``names`` (``model = X``, ``inlines = [X]``)."""
        return {
            sub.id
            for value in self._class_assignments(cls, *names)
            for sub in ast.walk(value)
            if isinstance(sub, ast.Name)
        }

    def _admin_kinds(self, classes: dict[str, ast.ClassDef]) -> dict[str, str | None]:
        """Which of the module's classes are admin classes, and of which sort.

        ``"inline"`` writes through its PARENT's formset and has no save hook of
        its own; ``"modeladmin"`` writes through its own ``save_model``. Resolved
        through locally-declared bases too, so a project-wide admin base class
        does not hide its subclasses from the arm.
        """
        kinds: dict[str, str | None] = {}

        def resolve(name: str, seen: frozenset[str]) -> str | None:
            if name in kinds:
                return kinds[name]
            cls = classes.get(name)
            if cls is None or name in seen:
                return None
            result: str | None = None
            for base in cls.bases:
                base_name = (
                    base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
                )
                if base_name is None:
                    continue
                if base_name in ADMIN_BASES:
                    result = "modeladmin" if base_name == "ModelAdmin" else "inline"
                elif base_name in classes:
                    result = resolve(base_name, seen | {name})
                if result is not None:
                    break
            kinds[name] = result
            return result

        for name in classes:
            resolve(name, frozenset())
        return kinds

    def _registrations(self, classes: dict[str, ast.ClassDef]) -> dict[str, set[str]]:
        """admin class name -> the models it is registered for.

        Covers both ``@admin.register(Model)`` on the class and the older
        ``admin.site.register(Model, SomeAdmin)`` call form.
        """
        registered: dict[str, set[str]] = {}
        for name, cls in classes.items():
            for decorator in cls.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if called != "register":
                    continue
                for arg in decorator.args:
                    if isinstance(arg, ast.Name):
                        registered.setdefault(name, set()).add(arg.id)
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called != "register" or len(node.args) < 2:
                continue
            model, admin_class = node.args[0], node.args[1]
            if isinstance(model, ast.Name) and isinstance(admin_class, ast.Name):
                registered.setdefault(admin_class.id, set()).add(model.id)
        return registered

    def _editable_settlement_fields(self, cls: ast.ClassDef) -> set[str]:
        """Which settlement fields this admin class leaves an operator able to write.

        Making every settlement field ``readonly`` (or keeping them out of an
        explicit ``fields``/``fieldsets``) is a legitimate way to satisfy the
        rule — such a class is not a writer and is not asked for anything.
        """
        candidate = set(self.a.all_fields)
        declared = self._class_strings(cls, "fields", "fieldsets")
        if any(True for _ in self._class_assignments(cls, "fields", "fieldsets")):
            candidate &= declared
        return candidate - self._class_strings(cls, "readonly_fields", "exclude")

    def _denies_delete(self, cls: ast.ClassDef) -> bool:
        """Whether this admin class has taken deletion away.

        ``can_delete = False`` on an inline, or a ``has_delete_permission`` that
        can only ever return False. Refusing the delete is a legitimate way to
        satisfy the delete obligation, exactly as making the columns readonly
        satisfies the save one — the rule is about what an operator can do, not
        about which methods happen to be defined.
        """
        for value in self._class_assignments(cls, "can_delete"):
            if isinstance(value, ast.Constant) and value.value is False:
                return True
        for stmt in cls.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if stmt.name != "has_delete_permission":
                continue
            returned = [sub.value for sub in ast.walk(stmt) if isinstance(sub, ast.Return)]
            if returned and all(
                isinstance(value, ast.Constant) and value.value is False for value in returned
            ):
                return True
        return False

    def _scan_admin(self) -> None:
        """A Django admin that can move a line's settlement owes the refresh.

        The admin was invisible to the write arm above because a ``ModelAdmin``
        never writes the way that arm looks for: ``super().save_model()`` hands
        the object to a ``ModelForm``, so there is no ``obj.quantity_ordered =``
        assignment and no ``create()``/``update()`` keyword to see. The write is
        real all the same — lowering ``quantity_ordered`` to what has arrived
        settles the line — so the obligation is derived from the CLASS instead:
        which model it edits, and which of that model's settlement fields it
        leaves writable.

        DELETION is a second and different shape. It writes no settlement field
        whatsoever, so it is not reachable by widening any rule about writes;
        what makes it a settlement transition is that the order is left owed
        less than it was. It gets its own obligation, discharged from the delete
        hooks — except on an inline, whose rows are deleted by the parent's
        formset save and are therefore owed from the parent's SAVE hooks.
        """
        classes = {
            node.name: node for node in ast.walk(self.tree) if isinstance(node, ast.ClassDef)
        }
        if not classes:
            return
        kinds = self._admin_kinds(classes)
        if not any(kinds.values()):
            return
        registered = self._registrations(classes)

        def hosts_of(inline: str) -> list[str]:
            return [
                host
                for host, cls in sorted(classes.items())
                if kinds.get(host) is not None and inline in self._class_references(cls, "inlines")
            ]

        # (admin class, the doors that must ALL be closed) -> why it owes them.
        owed: dict[tuple[str, tuple[str, ...]], list[str]] = {}

        def owe(admin_class: str, doors: tuple[str, ...], why: str) -> None:
            reasons = owed.setdefault((admin_class, doors), [])
            if why not in reasons:
                reasons.append(why)

        for name in sorted(classes):
            cls = classes[name]
            if kinds.get(name) is None:
                continue
            targets = self._class_references(cls, "model") | registered.get(name, set())
            if self.a.model_name not in targets:
                continue
            inline = kinds[name] == "inline"
            # An inline has no hook of its own: both its edits and its deletions
            # are performed by whichever ModelAdmin hosts it, in ``save_formset``.
            carriers = hosts_of(name) if inline else [name]

            # An inline's rows are both written and deleted by the parent's
            # formset save, so both of its obligations land on that one door.
            save_door = (ADMIN_FORMSET_HOOK,) if inline else (ADMIN_SAVE_HOOK,)

            editable = self._editable_settlement_fields(cls)
            if editable:
                columns = ", ".join(sorted(editable))
                for carrier in carriers or [name]:
                    owe(
                        carrier,
                        save_door,
                        (
                            f"the {name} inline it hosts leaves {columns} editable"
                            if carrier != name
                            else f"it leaves {columns} editable"
                        )
                        + f" on {self.a.model_name}",
                    )

            if not self._denies_delete(cls):
                struck = (
                    "a deleted line changes what the order is still owed, and writes no "
                    "settlement field while doing it"
                )
                if inline:
                    for carrier in carriers or [name]:
                        owe(
                            carrier,
                            save_door,
                            (
                                f"the {name} inline it hosts can delete {self.a.model_name} "
                                f"rows through this formset — {struck}"
                            ),
                        )
                else:
                    owe(
                        name,
                        ADMIN_DELETE_HOOKS,
                        f"it can delete {self.a.model_name} rows — {struck}",
                    )

        for (name, doors), reasons in sorted(owed.items()):
            self.admin_obligations.append(
                {
                    "path": self.rel,
                    "line": classes[name].lineno,
                    "admin": name,
                    "why": "; ".join(reasons),
                    "doors": doors,
                    # Every door has to be closed, not just one of them: Django
                    # dispatches to exactly one per action and never falls
                    # through, so a door left at its default is a door open.
                    "door_quals": {door: f"{self.rel}:{name}.{door}" for door in doors},
                    # ``save_related`` wraps the save doors within one request,
                    # so a refresh there closes them. Nothing wraps a delete.
                    "alternate_quals": (
                        [f"{self.rel}:{name}.{hook}" for hook in ADMIN_SAVE_ALTERNATES]
                        if doors != ADMIN_DELETE_HOOKS
                        else []
                    ),
                }
            )

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
        self._scan_admin()
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
    admin_obligations: list[dict] = []
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
        admin_obligations.extend(scanner.admin_obligations)

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

    report.findings.extend(_write_arm(anchor, functions, admin_obligations))
    report.findings.sort(key=lambda f: (f.path, f.line))
    return report


def _write_arm(
    anchor: Anchor,
    functions: dict[str, dict],
    admin_obligations: list[dict] | None = None,
) -> list[Finding]:
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

    ``admin_obligations`` carries the writers this arm's shape cannot see at
    all: a ``ModelAdmin`` writes through a ``ModelForm``, so it names no field
    and calls nothing this arm recognises, while an operator editing that form
    settles lines exactly as the API does. Those are derived from the admin
    CLASS (which model, which columns still writable) in
    :meth:`_PyScanner._scan_admin` and discharged here, through the same
    transitive ``reaches_refresh`` closure, so a hook that reaches the refresh
    via a helper satisfies its obligation like any other writer.
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

    for obligation in admin_obligations or []:
        if any(qual in reaches_refresh for qual in obligation["alternate_quals"]):
            continue
        open_doors = [
            door for door, qual in obligation["door_quals"].items() if qual not in reaches_refresh
        ]
        if not open_doors:
            continue
        findings.append(
            Finding(
                obligation["path"],
                obligation["line"],
                "write",
                f"{obligation['admin']} can change whether a line is settled — "
                f"{obligation['why']} — and {', '.join(open_doors)} "
                f"{'does' if len(open_doors) == 1 else 'do'} not reach {REFRESH}(), so an "
                f"admin action that settles the last outstanding line leaves the order "
                f"with a status claiming something its own lines no longer say. Django "
                f"dispatches to one hook per action and never falls through, so a hook "
                f"left at its default is a door open. Refusing the action here — readonly "
                f"columns, or no delete permission — satisfies this too",
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

    # The edges travel with the report. This arm has been blind to a write
    # SHAPE three times over; a clean run means "none of the shapes below the
    # first heading bypassed the derivation", never "there is nothing left".
    print("Write shapes this scan CAN see:")
    for shape in WRITE_SHAPES_SEEN:
        print(f"  + {shape}")
    print("Write shapes it CANNOT see — holes, not absences of sites:")
    for shape in WRITE_SHAPES_UNSEEN:
        print(f"  - {shape}")
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
