/**
 * Auth guard on the vendor pages (op-anonymous-read-posture).
 *
 * THIS GUARD IS NOT THE CONFIDENTIALITY BOUNDARY and this file must not be read
 * as asserting one: client-side routing keeps nothing from anyone, and a
 * determined caller talks to the API directly. What actually refuses them is
 * `SupplierViewSet` / `ItemSupplierViewSet` / `PriceHistoryViewSet` /
 * `SupplierAgreementViewSet` / `PurchaseOrderViewSet` being `IsAuthenticated`,
 * pinned in `backend/config/tests/test_anonymous_vendor_exposure.py`.
 *
 * What the guard is for is the visitor: without it a logged-out person
 * following a bookmark to /inventory/suppliers mounts the page, fires its
 * fetches, and is shown a shell full of 401 errors. Same reason /dashboard
 * carries it (op-3er), and this file is that file's twin.
 *
 * Renders the real <App /> so it exercises the actual route table.
 */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test } from 'vitest';
import App from '../App';

vi.mock('../pages/HomePage', () => ({ default: () => <div>Home Page</div> }));
vi.mock('../pages/SupplierListPage', () => ({ default: () => <div>Supplier List Page</div> }));
vi.mock('../pages/SupplierDetailPage', () => ({ default: () => <div>Supplier Detail Page</div> }));
vi.mock('../pages/SupplierFormPage', () => ({ default: () => <div>Supplier Form Page</div> }));
vi.mock('../pages/PurchaseOrderListPage', () => ({
  default: () => <div>Purchase Order List Page</div>,
}));
vi.mock('../pages/PurchaseOrderPage', () => ({ default: () => <div>Purchase Order Page</div> }));
vi.mock('../pages/PurchasingReportPage', () => ({
  default: () => <div>Purchasing Report Page</div>,
}));
// The scan surface must NOT be guarded — asserted below.
vi.mock('../pages/ScanPage', () => ({ default: () => <div>Scan Page</div> }));
vi.mock('../pages/InventoryItemDetailPage', () => ({
  default: () => <div>Item Detail Page</div>,
}));
vi.mock('../pages/TransparencyPage', () => ({ default: () => <div>Transparency Page</div> }));
vi.mock('../components/WorkspaceLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="workspace-layout">{children}</div>
  ),
}));

const renderAppAt = (path: string) => {
  window.history.pushState({}, '', path);
  return render(<App />);
};

/** Every route whose whole page is vendor identity or vendor money. */
const GUARDED = [
  ['/inventory/suppliers', 'Supplier List Page'],
  ['/inventory/suppliers/new', 'Supplier Form Page'],
  ['/inventory/suppliers/7', 'Supplier Detail Page'],
  ['/inventory/suppliers/7/edit', 'Supplier Form Page'],
  ['/purchasing/orders', 'Purchase Order List Page'],
  ['/purchasing/orders/po-1', 'Purchase Order Page'],
  ['/reports/purchasing', 'Purchasing Report Page'],
] as const;

/**
 * Routes that must stay reachable logged out. Guarding one of these would break
 * the flow the printed shelf QR codes exist for, which the captain's decision
 * explicitly protects — so they are asserted here, not merely left alone.
 */
const MUST_STAY_OPEN = [
  ['/inventory/scan/item-1', 'Scan Page'],
  ['/inventory/items/item-1', 'Item Detail Page'],
  ['/inventory/transparency', 'Transparency Page'],
] as const;

describe('Vendor page auth guard', () => {
  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    window.history.pushState({}, '', '/');
  });

  test.each(GUARDED)('sends a logged-out visitor from %s to the login home', (path, pageText) => {
    renderAppAt(path);

    expect(screen.getByText('Home Page')).toBeInTheDocument();
    expect(screen.queryByText(pageText)).not.toBeInTheDocument();
  });

  test.each(GUARDED)('renders %s for a signed-in visitor', (path, pageText) => {
    localStorage.setItem('token', 'jwt-access-token');

    renderAppAt(path);

    expect(screen.getByText(pageText)).toBeInTheDocument();
    expect(screen.queryByText('Home Page')).not.toBeInTheDocument();
  });

  test.each(MUST_STAY_OPEN)('leaves %s open to a logged-out visitor', (path, pageText) => {
    renderAppAt(path);

    expect(screen.getByText(pageText)).toBeInTheDocument();
  });
});
