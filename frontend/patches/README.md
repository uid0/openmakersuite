# patch-package patches

Patches in this directory are applied by the `postinstall` hook
(`"postinstall": "patch-package"` in `package.json`) after every `npm install` /
`npm ci`. CI's lint job runs `npm ci --ignore-scripts` and therefore skips them —
that job does not exercise any patched code.

Each patch must be minimal, upstream-shaped (i.e. something you would send as the
real fix), and documented below with the upstream issue and the removal
condition.

## `jsdom+30.0.0.patch`

**What:** guards `FONT_SIZE_REGEXP.exec()` against a `null` result in
`resolveLengthInPixels()` (`lib/jsdom/living/css/helpers/font-sizes.js`).

**Why:** jsdom 30.0.0 throws
`TypeError: object null is not iterable (cannot read property Symbol(Symbol.iterator))`
out of `window.getComputedStyle()` whenever an element's `font-size` is a
`calc()` containing a custom property jsdom cannot resolve — e.g.
`font-size: calc(var(--mantine-font-size-xs) * 1)`. `resolveCalc()` hands back
the still-unresolved `calc(...)` string, `FONT_SIZE_REGEXP` does not match it,
`exec()` returns `null`, and destructuring `null` throws.

Mantine v9 emits that CSS shape throughout, and both `@floating-ui/utils`
(Mantine popovers/Select) and `@testing-library/dom`'s `isInaccessible()` call
`getComputedStyle()` on every element during role queries — so this took out 28
tests across 13 files on jsdom 30 that pass on jsdom 29.

**The fix:** return `Number.NaN` when the regexp does not match, which is exactly
what the sibling `resolveFontSize()` in the same file already does for the same
unresolvable-value case. The caller then falls back to emitting the raw string,
restoring jsdom 29.1.1's behaviour byte for byte:

```
jsdom 29.1.1   getComputedStyle(el).fontSize === "calc(var(--mantine-font-size-xs) * 1)"
jsdom 30.0.0   throws TypeError
jsdom 30.0.0 + this patch === "calc(var(--mantine-font-size-xs) * 1)"
```

Resolved `calc()` values are unaffected (`calc(1rem + 2px)` still computes to
`"18px"`).

**Remove when:** jsdom ships a release containing this guard upstream.
