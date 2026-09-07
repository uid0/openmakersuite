/**
 * E2E: the Audit Feed row actually opens, in a real browser.
 *
 * WHY THIS EXISTS AS AN E2E SPEC AND NOT ONLY A UNIT TEST.
 *
 * `/admin/audit-feed` shipped with rows that could not be expanded at all:
 * `AuditFeedPage` passed Mantine's v7/v8 `<Collapse in={...}>`, and the
 * installed @mantine/core 9.x renamed that prop to `expanded`. The unmigrated
 * prop was dropped on the floor, so `expanded` was `undefined` and the panel
 * never opened — the metadata was unreadable from the UI.
 *
 * The jsdom test for it could not catch this, and did not: Mantine's Collapse
 * defaults to `keepMounted`, so the metadata node is in the document either
 * way, and the assertion only asked `toBeInTheDocument()`. The unit test has
 * since been rewritten to assert visibility, which does catch it.
 *
 * This spec covers the layer jsdom cannot reach at all. A collapsed Mantine
 * Collapse is hidden by LAYOUT — `height: 0` with `overflow: hidden` — and
 * jsdom computes no layout, so only a browser that actually lays the page out
 * can tell an open panel from a shut one by its box. Playwright's
 * `toBeVisible()` requires a non-empty bounding box, and `.click()` is a real
 * trusted input event rather than a dispatched one.
 *
 * The audit-feed response is stubbed at the network layer so the spec is
 * deterministic and needs no seeded backend rows. Nothing about the behaviour
 * under test — the expansion, or the condition badges — is stubbed: that is
 * all real page code rendered by a real browser.
 */
import { expect, test } from '@playwright/test';

const SCANNED_DAMAGED_RECEIPT = {
  domain: 'purchase_orders',
  action: 'po_receive_items',
  actor_id: 1,
  actor_username: 'receiving-clerk',
  created_at: '2026-09-07T14:32:00+00:00',
  entity_type: 'purchase_order',
  entity_id: '4417',
  notes: 'crushed corner, tape torn, two units unusable',
  // The shape `OrderReceiptViewSet.scan_barcode` writes: the `scan_barcode`
  // marker, and the condition the operator flagged at the scanner.
  metadata: {
    source: 'scan_barcode',
    scanned_upc: '0123456789012',
    is_damaged: true,
    is_expired: true,
    delivery_date: '2026-09-07T14:31:55+00:00',
    fully_received: false,
    received_items: [
      {
        purchase_order_item: 9912,
        quantity_received: 7,
        quantity_variance: 2,
        receipt_state: 'over_received',
      },
    ],
  },
};

const DESK_RECEIPT = {
  domain: 'purchase_orders',
  action: 'po_receive_items',
  actor_id: 1,
  actor_username: 'receiving-clerk',
  created_at: '2026-09-07T11:05:00+00:00',
  entity_type: 'purchase_order',
  entity_id: '4416',
  notes: '',
  // The desk path is never asked about condition, so it writes neither key.
  metadata: {
    delivery_date: '2026-09-07T11:05:00+00:00',
    tracking_number: '1Z999AA10123456784',
    carrier: 'UPS',
    fully_received: true,
    received_items: [
      {
        purchase_order_item: 9908,
        quantity_received: 12,
        quantity_variance: 0,
        receipt_state: 'exact',
        serials: [],
      },
    ],
  },
};

test.describe('Audit Feed rows', () => {
  test.beforeEach(async ({ page }) => {
    // The page gates on these before it will fetch anything.
    await page.addInitScript(() => {
      localStorage.setItem('is_staff', 'true');
      localStorage.setItem('is_superuser', 'true');
    });

    await page.route('**/api/dashboard/audit-feed/**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          count: 2,
          events: [SCANNED_DAMAGED_RECEIPT, DESK_RECEIPT],
        }),
      });
    });

    await page.goto('/admin/audit-feed');
  });

  test('clicking a row opens its metadata, and clicking again shuts it', async ({
    page,
  }) => {
    const row = page.getByTestId('audit-row-0');
    await expect(row).toBeVisible();

    // The metadata is MOUNTED from first paint (Mantine keepMounted) but
    // occupies no box while collapsed. Presence proves nothing here; the
    // bounding box is the whole question.
    const metadata = page.getByText('0123456789012', { exact: false }).last();
    await expect(metadata).toBeHidden();

    await row.click();
    await expect(metadata).toBeVisible();
    // And it is the real metadata blob, not just some node acquiring a box.
    await expect(metadata).toContainText('scan_barcode');

    await row.click();
    await expect(metadata).toBeHidden();
  });

  test('rows open independently of one another', async ({ page }) => {
    // Guards the per-row `expanded` set: one shared boolean would open both.
    const firstRow = page.getByTestId('audit-row-0');
    const secondRow = page.getByTestId('audit-row-1');

    await secondRow.click();

    await expect(page.getByText('1Z999AA10123456784').last()).toBeVisible();
    await expect(page.getByText('scan_barcode').last()).toBeHidden();

    await firstRow.click();
    await expect(page.getByText('scan_barcode').last()).toBeVisible();
  });

  test('a damaged or expired scanned receipt is badged without opening the row', async ({
    page,
  }) => {
    // The captain scrolls this feed looking for vendors to chase. If the
    // condition were only inside the collapsed blob, scrolling would not
    // show it.
    await expect(page.getByTestId('audit-condition-damaged-0')).toBeVisible();
    await expect(page.getByTestId('audit-condition-expired-0')).toBeVisible();
    await expect(page.getByTestId('audit-condition-damaged-0')).toHaveText(
      'Damaged',
    );

    // The desk row was never asked about condition and must not claim an
    // answer.
    await expect(page.getByTestId('audit-condition-damaged-1')).toHaveCount(0);
    await expect(page.getByTestId('audit-condition-expired-1')).toHaveCount(0);
  });
});
