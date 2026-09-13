/**
 * The web half of the pack-size single-owner gate
 * (`eslint-rules/pack-size-single-owner.mjs`), exercised on constructed
 * sources, mirroring `backend/inventory/tests/test_pack_size_single_owner.py`.
 *
 * A gate that cannot see the bypass it was built to catch passes vacuously, so
 * each shape below is linted with the rule itself. None of the rule cases
 * assert anything about the repository's own source: `npm run lint` is what
 * enforces the rule on the tree.
 */

import { existsSync } from 'node:fs';
import path from 'node:path';

import tsParser from '@typescript-eslint/parser';
import { ESLint, RuleTester } from 'eslint';

import {
  ALLOWED,
  packSizeSingleOwner,
} from '../../../eslint-rules/pack-size-single-owner.mjs';

const FRONTEND_DIR = path.resolve(__dirname, '../../..');

/** A module nobody allowlisted, and one that is allowlisted for two reads. */
const NEW_MODULE = path.join(FRONTEND_DIR, 'src/utils/planted.ts');
const SCAN_PAGE = path.join(FRONTEND_DIR, 'src/pages/ScanPage.tsx');

const tester = new RuleTester({
  languageOptions: {
    parser: tsParser,
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
});

const newReader = { messageId: 'newReader' };
const countChanged = { messageId: 'countChanged' };

describe('pack-size single-owner lint rule', () => {
  tester.run('pack-size-single-owner', packSizeSingleOwner as never, {
    valid: [
      // Writing the column cannot turn "we do not know" into a number.
      { filename: NEW_MODULE, code: 'link.quantity_per_package = 5;' },
      // Building a payload names the field; it does not read one.
      { filename: NEW_MODULE, code: 'const payload = { quantity_per_package: 5 };' },
      { filename: NEW_MODULE, code: 'send({ quantity_per_package: form.units });' },
      // Types, prose and strings.
      { filename: NEW_MODULE, code: 'interface Link { quantity_per_package: number }' },
      { filename: NEW_MODULE, code: "type Units = Link['quantity_per_package'];" },
      { filename: NEW_MODULE, code: "const label = 'quantity_per_package'; // quantity_per_package" },
      // An unrelated column never trips the gate.
      { filename: NEW_MODULE, code: 'const n = link.quantity_per_pallet + link.package_cost;' },
      // A key whose runtime value cannot be established is not guessed at.
      { filename: NEW_MODULE, code: 'const units = link[fieldName];' },
      // An allowlisted module reading exactly its allowlisted number of times.
      {
        filename: SCAN_PAGE,
        code: 'const a = s.quantity_per_package; const b = `${s.quantity_per_package}`;',
      },
    ],
    invalid: [
      // A plain read, and the `|| 1` collapse that motivated the backend gate.
      { filename: NEW_MODULE, code: 'const units = link.quantity_per_package || 1;', errors: [newReader] },
      { filename: NEW_MODULE, code: 'const units = link?.quantity_per_package;', errors: [newReader] },
      // The same read, spelled dynamically.
      { filename: NEW_MODULE, code: "const units = link['quantity_per_package'];", errors: [newReader] },
      { filename: NEW_MODULE, code: 'const units = link[`quantity_per_package`];', errors: [newReader] },
      {
        filename: NEW_MODULE,
        code: "const key = 'quantity_per_package'; const units = link[key] || 1;",
        errors: [newReader],
      },
      {
        filename: NEW_MODULE,
        code: 'const key = `quantity_per_package`; const units = link[key];',
        errors: [newReader],
      },
      // Destructuring is a read, in a declaration and in a parameter list.
      { filename: NEW_MODULE, code: 'const { quantity_per_package } = link;', errors: [newReader] },
      {
        filename: NEW_MODULE,
        code:
          "const key = 'quantity_per_package'; const { [key]: units } = link; const cases = units || 1;",
        errors: [newReader],
      },
      {
        filename: NEW_MODULE,
        code: 'const f = ({ quantity_per_package: units }: Link) => units === 0;',
        errors: [newReader],
      },
      // A compound assignment reads before it writes.
      { filename: NEW_MODULE, code: 'link.quantity_per_package += 1;', errors: [newReader] },
      // A payload built FROM a read still counts the read, not the key.
      {
        filename: NEW_MODULE,
        code: 'send({ quantity_per_package: link.quantity_per_package || 1 });',
        errors: [newReader],
      },
      // Each site is reported.
      {
        filename: NEW_MODULE,
        code: 'const a = x.quantity_per_package; const b = y.quantity_per_package;',
        errors: [newReader, newReader],
      },
      // The owner of the wording may not re-derive the state either.
      {
        filename: path.join(FRONTEND_DIR, 'src/utils/caseSize.ts'),
        code: "const zero = (s: Link) => s.quantity_per_package === 0 ? 'recorded_zero' : 'known';",
        errors: [newReader],
      },
      // One more read in a module already listed.
      {
        filename: SCAN_PAGE,
        code: 'const a = s.quantity_per_package; const b = s.quantity_per_package; const c = s.quantity_per_package || 1;',
        errors: [countChanged],
      },
      // One fewer: the list must not go stale and leave room for a later read.
      { filename: SCAN_PAGE, code: 'const a = s.quantity_per_package;', errors: [countChanged] },
    ],
  });

  it('allowlists only modules that exist, each with a reason', () => {
    const entries = Object.entries(ALLOWED);
    expect(entries.length).toBeGreaterThan(0);
    for (const [file, [count, reason]] of entries) {
      expect(existsSync(path.join(FRONTEND_DIR, file)), `${file} is allowlisted but missing`).toBe(true);
      expect(count).toBeGreaterThan(0);
      expect(reason.trim()).not.toBe('');
    }
  });

  it('is enabled as an error for application sources by the lint config', async () => {
    const eslint = new ESLint({ cwd: FRONTEND_DIR });
    for (const file of ['src/pages/ScanPage.tsx', 'src/utils/caseSize.ts']) {
      const config = await eslint.calculateConfigForFile(file);
      expect(config.rules['oms-local/pack-size-single-owner'], file).toEqual([2]);
    }
  });
});
