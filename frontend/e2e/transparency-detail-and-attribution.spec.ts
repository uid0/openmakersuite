/**
 * E2E: the public transparency page, in a real browser, for both its readers.
 *
 * WHY AN E2E SPEC. Two of the three things this pins are decided by LAYOUT and
 * by the response body, neither of which a jsdom unit test settles:
 *
 *  - the SIGNED-IN reader must actually SEE the per-order detail. A jsdom
 *    assertion that a node is in the document does not distinguish a rendered
 *    row from one the page mounted and never gave a box;
 *  - the ANONYMOUS reader must see the aggregates and nothing vendor-shaped,
 *    and the proof has to run over the bytes the SERVER sends, not over a
 *    fixture written to match the page.
 *
 * Both bodies in `fixtures/transparency/` were RECORDED from a real backend —
 * see the README there. Nothing about the behaviour under test is stubbed: the
 * network response is, and everything downstream of it is the real page.
 */
import { expect, test } from '@playwright/test';

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/**
 * Read from disk rather than imported: Playwright runs these specs as ESM, and
 * a JSON import there needs an import attribute the repo's TS config does not
 * emit. Reading the file keeps the fixture the RECORDED bytes either way.
 */
const recorded = (name: string) =>
  JSON.parse(
    readFileSync(
      fileURLToPath(new URL(`./fixtures/transparency/${name}.json`, import.meta.url)),
      'utf8',
    ),
  );

const anonymousBody = recorded('anonymous');
const signedInBody = recorded('signed-in');

const TRANSPARENCY = '/inventory/transparency';

/** The vendor's name in the recorded pair — the sentinel for "identity leaked". */
const VENDOR = signedInBody.orders[0].item_supplier_choice.supplier_name as string;

const serve = (body: unknown) => async (page: import('@playwright/test').Page) => {
  await page.route('**/api/reorders/analytics/transparency/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
  await page.goto(TRANSPARENCY);
};

test.describe('a signed-in reader', () => {
  test.beforeEach(async ({ page }) => {
    await serve(signedInBody)(page);
  });

  test('sees the per-order detail, laid out and visible', async ({ page }) => {
    // The paid order's own facts.
    await expect(page.getByText('Actual Cost:').first()).toBeVisible();
    await expect(page.getByText('$150.25').first()).toBeVisible();
    await expect(page.getByText('Order #: ORD-2026-0042')).toBeVisible();

    // The ledger table gets its two gated columns.
    await expect(
      page.getByRole('columnheader', { name: 'Item supplier today' }),
    ).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Paid' })).toBeVisible();
    await expect(page.getByText(VENDOR).first()).toBeVisible();

    // And no "sign in to see" note, which is the anonymous reader's.
    await expect(page.getByTestId('ledger-vendor-withheld')).toHaveCount(0);
  });

  test('publishes a donated order as $0.00 and never as a bare "0"', async ({ page }) => {
    // `ORD-2026-0043` carries a RECORDED `actual_cost` of 0.00. The card used
    // to guard it on truthiness, which in JSX prints the number `0` itself.
    const card = page.locator('.order-card', { hasText: 'Donated by a member.' });
    await expect(card).toBeVisible();

    const financials = card.locator('.financial-info');
    await expect(financials).toHaveText('Actual Cost:$0.00Cost per Unit:$0.00');
  });

  test('never presents the item\'s supplier or price as the order\'s own', async ({
    page,
  }) => {
    const card = page.locator('.order-card').first();

    await expect(card.getByText('Item supplier today:')).toBeVisible();
    await expect(card.getByText(/Same quantity at today's price:/)).toBeVisible();
    await expect(
      card.locator('.item-scope-note'),
    ).toHaveText("The item's supplier and price as of now — not this order's.");

    // The three labels that claimed an order-level fact it never had.
    await expect(page.getByText('Supplier:', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Estimated Cost:')).toHaveCount(0);
    await expect(page.getByText('Cost Variance:')).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText('budget');
  });
});

test.describe('a reader with no session', () => {
  test.beforeEach(async ({ page }) => {
    await serve(anonymousBody)(page);
  });

  test('keeps the totals and the items, and is told where the rest went', async ({
    page,
  }) => {
    await expect(page.getByText('Total Spent')).toBeVisible();
    await expect(page.getByText('$150.25').first()).toBeVisible();
    await expect(
      page.getByText(signedInBody.orders[0].item_name as string).first(),
    ).toBeVisible();
    await expect(page.getByTestId('ledger-vendor-withheld')).toBeVisible();
  });

  test('is shown no vendor identity, no vendor money, and no "N/A" standing in for one', async ({
    page,
  }) => {
    const body = page.locator('body');
    // Identity, per-order money and the paperwork numbers, checked against the
    // SIGNED-IN body's own values so this cannot pass on a page that simply
    // renders nothing.
    await expect(body).not.toContainText(VENDOR);
    await expect(body).not.toContainText('ORD-2026-0042');
    await expect(body).not.toContainText('INV-2026-0042');
    await expect(body).not.toContainText('$0.00');
    // "N/A" and "$NaN" are claims about the DATA where the truth is a fact
    // about the reader; the columns are dropped instead.
    await expect(body).not.toContainText('NaN');
    await expect(
      page.getByRole('columnheader', { name: 'Item supplier today' }),
    ).toHaveCount(0);
    await expect(page.getByRole('columnheader', { name: 'Paid' })).toHaveCount(0);
  });
});
