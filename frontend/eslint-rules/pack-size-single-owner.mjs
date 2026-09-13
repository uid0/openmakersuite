/**
 * A web reader of `quantity_per_package` added outside this list fails lint.
 *
 * The frontend twin of `backend/inventory/tests/test_pack_size_single_owner.py`,
 * and deliberately the SAME rule rather than a broader one. "How many units are
 * in a box" has one owner, `backend/inventory/services/pack_size.py`, which
 * serializes its answer as `case_size_state`; `src/utils/caseSize.ts` is the one
 * place the web turns that state into words. A web module that reads the raw
 * column can re-derive the state itself — `|| 1` turning a recorded zero into
 * one unit per box is the shape `ScanPage` shipped after every backend reader
 * had moved onto the derivation — and that second copy is free to drift from
 * the one that decides.
 *
 * So, like the backend walk, this counts real READS of the column in every
 * linted module (`eslint src`, which already skips `src/__tests__`) and compares
 * the count per file with {@link ALLOWED}. A new reader anywhere — a new module,
 * or one more read in a module already listed — fails until it goes through the
 * server's state or is added here deliberately, with a reason. A listed module
 * that reads FEWER times fails too, so the list cannot go stale and quietly
 * leave room for a later read.
 * Like the backend gate, this per-file count cannot detect a same-file swap
 * that removes one approved read and adds one derivation. Changes to reads in
 * an allowlisted module therefore remain a review responsibility.
 *
 * **Scope: `quantity_per_package` only**, exactly as the backend gate. The
 * `caseSize.ts` header also rules out re-deriving the state from supplier-link
 * counts or `is_active` / `is_discontinued`; those reads have legitimate uses
 * everywhere (lists, filters, badges) and neither gate enforces them.
 *
 * **What counts as a read** — the JavaScript spellings of what the backend
 * counts:
 *
 * - `link.quantity_per_package` and `link?.quantity_per_package` — property
 *   access, except as the target of a plain `=` (a WRITE, like a Python Store).
 * - `link['quantity_per_package']` — the same read spelled dynamically, the
 *   `getattr` of this language.
 * - `const { quantity_per_package } = link`, including in a parameter list —
 *   destructuring IS a read, and Python has no spelling of it to mirror.
 * A dynamic spelling is counted when ESLint's scope manager resolves its key
 * statically to the column name: a literal, an expression-free template, or a
 * `const` bound to either. Runtime computations (`let`, imports, function
 * results, concatenation, `Reflect.get`, or lodash `get`) are not counted, just
 * as the backend gate counts `getattr` only with a literal name.
 *
 * NOT counted, for the backend's reasons: an object-literal key
 * (`{ quantity_per_package: 5 }` builds a payload, like `create(...)` kwargs),
 * a type or interface member, and the name in a string or comment. Naming the
 * field cannot fabricate a number.
 *
 * The walk is syntactic, by property name, as the backend's is by attribute
 * name: which object the property is read off does not change whether the read
 * can turn "we do not know" into a number.
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const COLUMN = 'quantity_per_package';

/** `frontend/`, so allowlist keys do not depend on the directory eslint ran from. */
const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/**
 * Reads of the column that are NOT a case-size derivation, with why. Paths are
 * relative to `frontend/`. A DERIVATION belongs on the server, worded through
 * `src/utils/caseSize.ts` — which is not listed, because it reads none.
 *
 * @type {Record<string, [number, string]>}
 */
export const ALLOWED = {
  'src/utils/supplierRelationships.ts': [
    4,
    'The supplier-link editor: copies the saved row into the editable one ' +
      '(`relationshipFromSaved`), sends it back verbatim (`relationshipPayload`), ' +
      'and compares the two (`relationshipChanged`, 2). A copy, a write path and ' +
      'a comparison — none of them an answer.',
  ],
  'src/components/SupplierRelationshipForm.tsx': [
    1,
    'The "Quantity per Package" input shows what the row records, including a ' +
      'zero, so the operator can correct it. That is the write path.',
  ],
  'src/pages/ScanPage.tsx': [
    2,
    "`packSizeOf` sizes a reorder from ONE supplier row the operator picked, " +
      'guarding `>= 1` explicitly so a recorded zero is refused rather than ' +
      'multiplied by; `packSizeRefusal` quotes that recorded value back in the ' +
      'refusal. A row\'s own column, not an item-level case-size answer — the ' +
      'same standing as `ItemSupplier`\'s arithmetic on its own row in the ' +
      'backend list.',
  ],
  'src/pages/PurchaseOrderFormPage.tsx': [
    12,
    'Purchase-order lines for ONE supplier link each. Every read asks only ' +
      'whether that link declares a case (`> 1`) and, when it does, multiplies ' +
      'cases, units and costs by it — the web face of `declares_a_case`, which ' +
      'the backend\'s order-pad rounding asks. The `|| 1` spellings send a ' +
      'recorded zero or an absent value down the units-only branch; no zero is ' +
      'ever multiplied by, and none is shown as a case size.',
  ],
};

const toPosix = (p) => p.split(path.sep).join('/');

/** A string expression whose value ESLint can establish without execution. */
const staticString = (node, sourceCode, seen = new Set()) => {
  if (node.type === 'Literal' && typeof node.value === 'string') return node.value;
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) {
    return node.quasis[0].value.cooked;
  }
  if (node.type !== 'Identifier' || seen.has(node)) return null;

  seen.add(node);
  let scope = sourceCode.getScope(node);
  while (scope) {
    const variable = scope.set.get(node.name);
    if (variable) {
      if (variable.defs.length !== 1 || variable.defs[0].type !== 'Variable') return null;
      const declarator = variable.defs[0].node;
      if (declarator.parent.kind !== 'const' || declarator.id.type !== 'Identifier') return null;
      return declarator.init ? staticString(declarator.init, sourceCode, seen) : null;
    }
    scope = scope.upper;
  }
  return null;
};

/** The property name a member expression reads, when it is statically known. */
const memberName = (node, sourceCode) => {
  const { property } = node;
  if (!node.computed) return property.type === 'Identifier' ? property.name : null;
  return staticString(property, sourceCode);
};

/** The key a destructuring property names, when it is statically known. */
const patternKeyName = (prop, sourceCode) => {
  const { key } = prop;
  if (!prop.computed && key.type === 'Identifier') return key.name;
  return staticString(key, sourceCode);
};

/**
 * Whether this member expression is only ever WRITTEN: the target of a plain
 * `=`, or a slot in a destructuring assignment. `x.col += 1` reads it first,
 * so a compound operator is still a read.
 */
const isWriteTarget = (node) => {
  const { parent } = node;
  if (parent.type === 'AssignmentExpression') return parent.left === node && parent.operator === '=';
  if (parent.type === 'ArrayPattern' || parent.type === 'RestElement') return true;
  if (parent.type === 'AssignmentPattern') return parent.left === node;
  if (parent.type === 'Property' && parent.parent.type === 'ObjectPattern') {
    return parent.value === node;
  }
  return false;
};

/** @type {import('eslint').Rule.RuleModule} */
export const packSizeSingleOwner = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Keep web reads of quantity_per_package to the allowlisted modules, so the case-size ' +
        "state stays the server's (src/utils/caseSize.ts words it)",
    },
    schema: [],
    messages: {
      newReader:
        '`{{file}}` reads `{{column}}` directly. The case size has ONE owner, ' +
        "backend/inventory/services/pack_size.py: word the server's `case_size_state` through " +
        'src/utils/caseSize.ts instead of re-deriving it. A read of the column cannot tell a ' +
        'pack size nobody recorded from a recorded zero, and `|| 1` collapses both into a ' +
        'number. If this is a verbatim copy or a write path, add it to ALLOWED in ' +
        'eslint-rules/pack-size-single-owner.mjs with the reason.',
      countChanged:
        '`{{file}}` reads `{{column}}` {{found}} time(s) (lines {{lines}}) but ALLOWED in ' +
        'eslint-rules/pack-size-single-owner.mjs expects {{expected}}. If you added a ' +
        "derivation, word the server's `case_size_state` through src/utils/caseSize.ts " +
        'instead. If you added or removed a verbatim copy or a write path, update the count ' +
        'there with the reason.',
    },
  },

  create(context) {
    const sourceCode = context.sourceCode;
    const file = toPosix(path.relative(FRONTEND_DIR, context.filename));
    const allowed = Object.hasOwn(ALLOWED, file) ? ALLOWED[file][0] : null;
    const reads = [];

    return {
      MemberExpression(node) {
        if (memberName(node, sourceCode) === COLUMN && !isWriteTarget(node)) reads.push(node);
      },
      'ObjectPattern > Property'(node) {
        if (patternKeyName(node, sourceCode) === COLUMN) reads.push(node);
      },
      'Program:exit'(program) {
        if (allowed === null) {
          for (const node of reads) {
            context.report({ node, messageId: 'newReader', data: { file, column: COLUMN } });
          }
          return;
        }
        if (reads.length !== allowed) {
          context.report({
            node: reads[0] ?? program,
            messageId: 'countChanged',
            data: {
              file,
              column: COLUMN,
              found: String(reads.length),
              expected: String(allowed),
              lines: reads.map((n) => n.loc.start.line).join(', ') || 'none',
            },
          });
        }
      },
    };
  },
};

export default {
  meta: { name: 'oms-local' },
  rules: { 'pack-size-single-owner': packSizeSingleOwner },
};
