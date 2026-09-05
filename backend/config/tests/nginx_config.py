"""A normalized semantic model of an nginx config, for tests that assert MEANING.

``nginx/templates/default.conf.template`` is a machine-consumed declarative
artefact, and the repo's test-quality rule says such a thing is checked by
invoking its real consumer or by parsing it into a normalized model — never by
regexing its text. A raw substring search over this file passes on a
``${VAR}``-templated prefix, on a differently indented closing brace, and on an
``expires 7d`` inherited from an enclosing block, which is exactly the set of
edits a gate over ``/media/`` has to survive.

So this parses. It gives ``config/tests/test_protected_media.py`` three things
the text cannot:

* :func:`render`, which applies the ``${VAR}`` substitution the container's
  entrypoint does, so the model is of what nginx actually reads;
* :meth:`Block.effective`, which resolves nginx's inheritance rule — a directive
  is inherited from the enclosing level only when the current level declares
  none of that name — so "is this response publicly cacheable?" is answered the
  way the server answers it rather than by looking for a line;
* :meth:`Block.match_location`, which implements nginx's location-selection
  order (exact ``=``, then longest ``^~`` prefix, then regex in declaration
  order, then longest plain prefix). Asserting on the block nginx WOULD SELECT
  for a URI is the only form of this that a reordering cannot fool.

Scope: enough nginx grammar for this repo's own config — comments, quoted
arguments, directives and nested blocks. ``include`` is not followed (this
template has none); a directive named ``include`` appears in the model like any
other, so a test that cares can assert it is absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

_TOKEN = re.compile(
    r"""
    \#[^\n]*            # comment to end of line
    | "(?:\\.|[^"\\])*"   # double-quoted argument
    | '(?:\\.|[^'\\])*'   # single-quoted argument
    | [{};]               # structure
    | [^\s{};]+           # bare word
    """,
    re.X,
)


def render(template: str, variables: dict[str, str]) -> str:
    """Apply ``${NAME}`` substitution the way the nginx image's entrypoint does.

    An unset name is left as written rather than blanked, so a test reading the
    result can tell "this deployment has no such variable" from "this prefix is
    templated and I cannot resolve it".
    """
    return re.sub(
        r"\$\{(\w+)\}",
        lambda m: variables.get(m.group(1), m.group(0)),
        template,
    )


def _tokens(text: str) -> Iterator[str]:
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if token.startswith("#"):
            continue
        yield token


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


@dataclass(frozen=True)
class Directive:
    """One ``name arg arg;`` statement."""

    name: str
    args: tuple[str, ...]

    @property
    def value(self) -> str:
        return " ".join(self.args)


@dataclass
class Block:
    """One ``name args { ... }`` block, and everything declared inside it."""

    name: str
    args: tuple[str, ...]
    directives: list[Directive] = field(default_factory=list)
    blocks: list["Block"] = field(default_factory=list)
    parent: Optional["Block"] = field(default=None, repr=False, compare=False)

    # ── what this level declares ────────────────────────────────────────────

    def declared(self, name: str) -> list[Directive]:
        """Every directive of that name declared AT THIS LEVEL."""
        return [d for d in self.directives if d.name == name]

    def effective(self, name: str) -> list[Directive]:
        """The directives nginx applies here, following its inheritance rule.

        A level that declares the directive at all overrides the enclosing
        level entirely — it does not merge. That is why an ``expires 7d`` in a
        wider block reaches a narrower one that says nothing about caching, and
        why the narrower block saying ``expires -1`` is enough to stop it.
        """
        here = self.declared(name)
        if here:
            return here
        if self.parent is None:
            return []
        return self.parent.effective(name)

    def header(self, header_name: str) -> Optional[str]:
        """The value of an effective ``add_header`` for ``header_name``.

        ``add_header`` inherits by the same all-or-nothing rule as any other
        directive, so this asks :meth:`effective` rather than scanning upward.
        """
        wanted = header_name.lower()
        for directive in self.effective("add_header"):
            if directive.args and directive.args[0].lower() == wanted:
                return " ".join(directive.args[1:2])
        return None

    # ── location selection ──────────────────────────────────────────────────

    @property
    def locations(self) -> list["Block"]:
        return [b for b in self.blocks if b.name == "location"]

    def match_location(self, uri: str) -> Optional["Block"]:
        """The ``location`` block nginx would select for ``uri``.

        Implements the documented order: an exact ``=`` match wins outright;
        otherwise the longest matching prefix is remembered, and if that one
        carries ``^~`` it wins and regexes are skipped; otherwise regexes are
        tried in declaration order; otherwise the longest prefix wins.
        """
        best_prefix: Optional[Block] = None
        best_length = -1
        regexes: list[Block] = []

        for location in self.locations:
            modifier, pattern = _location_signature(location)
            if modifier == "=":
                if uri == pattern:
                    return location
            elif modifier in ("~", "~*"):
                regexes.append(location)
            else:
                if uri.startswith(pattern) and len(pattern) > best_length:
                    best_prefix = location
                    best_length = len(pattern)

        if best_prefix is not None and _location_signature(best_prefix)[0] == "^~":
            return best_prefix

        for location in regexes:
            modifier, pattern = _location_signature(location)
            flags = re.I if modifier == "~*" else 0
            if re.search(pattern, uri, flags):
                return location

        return best_prefix


def _location_signature(location: Block) -> tuple[str, str]:
    """``(modifier, pattern)`` for a ``location`` block; modifier ``""`` if none."""
    args = location.args
    if args and args[0] in ("=", "^~", "~", "~*"):
        return args[0], args[1] if len(args) > 1 else ""
    return "", args[0] if args else ""


def parse(text: str) -> Block:
    """Parse an nginx config into a :class:`Block` tree rooted at ``main``."""
    root = Block(name="main", args=())
    stack = [root]
    words: list[str] = []

    for token in _tokens(text):
        if token == ";":
            if words:
                stack[-1].directives.append(Directive(words[0], tuple(words[1:])))
            words = []
        elif token == "{":
            if not words:
                raise ValueError("nginx config: '{' with no block name")
            block = Block(name=words[0], args=tuple(words[1:]), parent=stack[-1])
            stack[-1].blocks.append(block)
            stack.append(block)
            words = []
        elif token == "}":
            if len(stack) == 1:
                raise ValueError("nginx config: unbalanced '}'")
            stack.pop()
            words = []
        else:
            words.append(_unquote(token))

    if len(stack) != 1:
        raise ValueError("nginx config: unclosed block")
    return root


def servers(root: Block) -> list[Block]:
    """Every ``server`` block, at any depth."""
    found: list[Block] = []

    def walk(block: Block) -> None:
        for child in block.blocks:
            if child.name == "server":
                found.append(child)
            walk(child)

    walk(root)
    return found
