/**
 * Main App Component
 */
import * as Sentry from '@sentry/react';
import { Navigate, Route, BrowserRouter as Router, Routes, useParams } from 'react-router-dom';
import ErrorFallback from './components/ErrorFallback';
import WorkspaceLayout from './components/WorkspaceLayout';
import AdminDashboard from './pages/AdminDashboard';
import AssetDetailPage from './pages/AssetDetailPage';
import AssetFormPage from './pages/AssetFormPage';
import MaintenanceItemFormPage from './pages/MaintenanceItemFormPage';
import AssetReportPage from './pages/AssetReportPage';
import AssetScanPage from './pages/AssetScanPage';
import AssetsPage from './pages/AssetsPage';
import CategoryFormPage from './pages/CategoryFormPage';
import CategoryListPage from './pages/CategoryListPage';
import ChecklistCompletionPage from './pages/ChecklistCompletionPage';
import CodeEntryPage from './pages/CodeEntryPage';
import DashboardPage from './pages/DashboardPage';
import DonationItemScanPage from './pages/DonationItemScanPage';
import FixtureScanPage from './pages/FixtureScanPage';
import HomePage from './pages/HomePage';
import InventoryItemDetailPage from './pages/InventoryItemDetailPage';
import InventoryItemFormPage from './pages/InventoryItemFormPage';
import InventoryListPage from './pages/InventoryListPage';
import InventoryReportPage from './pages/InventoryReportPage';
import InventoryReconciliationPage from './pages/InventoryReconciliationPage';
import LocationDetailPage from './pages/LocationDetailPage';
import LocationFormPage from './pages/LocationFormPage';
import LocationListPage from './pages/LocationListPage';
import LocationScanPage from './pages/LocationScanPage';
import LogisticsDashboard from './pages/LogisticsDashboard';
import MakerBoxAdminPage from './pages/MakerBoxAdminPage';
import MakerBoxScanPage from './pages/MakerBoxScanPage';
import MaintenanceDashboard from './pages/MaintenanceDashboard';
import WorkOrderPage from './pages/WorkOrderPage';
import PurchaseOrderFormPage from './pages/PurchaseOrderFormPage';
import PurchaseOrderListPage from './pages/PurchaseOrderListPage';
import PurchaseOrderPage from './pages/PurchaseOrderPage';
import PurchasingReportPage from './pages/PurchasingReportPage';
import ScanPage from './pages/ScanPage';
import SIGDashboard from './pages/SIGDashboard';
import SiteSettingsPage from './pages/SiteSettingsPage';
import SupplierDetailPage from './pages/SupplierDetailPage';
import SupplierFormPage from './pages/SupplierFormPage';
import SupplierListPage from './pages/SupplierListPage';
import TaxReceiptLookupPage from './pages/TaxReceiptLookupPage';
import ThanksPage from './pages/ThanksPage';
import TransparencyPage from './pages/TransparencyPage';
import KioskDisplayPage from './pages/KioskDisplayPage';
import ScreenEditPage from './pages/ScreenEditPage';
import ScreensListPage from './pages/ScreensListPage';
import TVDashboard from './pages/TVDashboard';
import UserProfilePage from './pages/UserProfilePage';
import WebhookDetailPage from './pages/WebhookDetailPage';
import WebhookFormPage from './pages/WebhookFormPage';
import WebhookListPage from './pages/WebhookListPage';
import './styles/App.css';

// Wrap routes with Sentry for better error tracking
const SentryRoutes = Sentry.withSentryRouting(Routes);

// Redirect components for dynamic routes
const RedirectTVDashboardLocation = () => {
  const { location } = useParams();
  return <Navigate to={`/facilities/tv-dashboard/${location}`} replace />;
};

const RedirectPurchaseOrder = () => {
  const { orderId } = useParams();
  return <Navigate to={`/purchasing/orders/${orderId}`} replace />;
};

const RedirectSIGDashboard = () => {
  const { sigId } = useParams();
  return <Navigate to={`/sigs/dashboard/${sigId}`} replace />;
};

const RedirectScanItem = () => {
  const { itemId } = useParams();
  return <Navigate to={`/inventory/scan/${itemId}`} replace />;
};

const RedirectScanFixture = () => {
  const { fixtureId } = useParams();
  return <Navigate to={`/inventory/scan/fixture/${fixtureId}`} replace />;
};

const RedirectScanAsset = () => {
  const { assetId } = useParams();
  return <Navigate to={`/inventory/scan/asset/${assetId}`} replace />;
};

const RedirectScanLocation = () => {
  const { locationId } = useParams();
  return <Navigate to={`/inventory/scan/location/${locationId}`} replace />;
};

const RedirectScanDonationItem = () => {
  const { itemId } = useParams();
  return <Navigate to={`/inventory/scan/donation-item/${itemId}`} replace />;
};

const RedirectChecklist = () => {
  const { checklistId, completionId } = useParams();
  return <Navigate to={`/facilities/checklist/${checklistId}/complete/${completionId}`} replace />;
};

function AppContent() {
  return (
    <div className="App">
      <Sentry.ErrorBoundary
        fallback={({ error, resetError, eventId }) => (
          <ErrorFallback error={error} resetError={resetError} eventId={eventId} />
        )}
        showDialog={false}
      >
        <SentryRoutes>
          {/* Home/Landing */}
          <Route path="/" element={<HomePage />} />

          {/* Redirects for backward compatibility */}
          <Route path="/tv-dashboard" element={<Navigate to="/facilities/tv-dashboard" replace />} />
          <Route path="/tv-dashboard/:location" element={<RedirectTVDashboardLocation />} />
          <Route path="/tv-logistics" element={<Navigate to="/facilities/logistics" replace />} />

          {/* Inventory Workspace */}
          <Route path="/inventory" element={<WorkspaceLayout><HomePage /></WorkspaceLayout>} />
          <Route path="/dashboard" element={<WorkspaceLayout><DashboardPage /></WorkspaceLayout>} />
          <Route path="/inventory/items" element={<WorkspaceLayout><InventoryListPage /></WorkspaceLayout>} />
          <Route path="/inventory/items/new" element={<WorkspaceLayout><InventoryItemFormPage /></WorkspaceLayout>} />
          <Route path="/inventory/items/:id" element={<WorkspaceLayout><InventoryItemDetailPage /></WorkspaceLayout>} />
          <Route path="/inventory/items/:id/edit" element={<WorkspaceLayout><InventoryItemFormPage /></WorkspaceLayout>} />
          <Route path="/inventory/suppliers" element={<WorkspaceLayout><SupplierListPage /></WorkspaceLayout>} />
          <Route path="/inventory/suppliers/new" element={<WorkspaceLayout><SupplierFormPage /></WorkspaceLayout>} />
          <Route path="/inventory/suppliers/:id" element={<WorkspaceLayout><SupplierDetailPage /></WorkspaceLayout>} />
          <Route path="/inventory/suppliers/:id/edit" element={<WorkspaceLayout><SupplierFormPage /></WorkspaceLayout>} />
          <Route path="/inventory/assets" element={<WorkspaceLayout><AssetsPage /></WorkspaceLayout>} />
          <Route path="/inventory/admin" element={<WorkspaceLayout><AdminDashboard /></WorkspaceLayout>} />
          <Route path="/inventory/code-entry" element={<Navigate to="/inventory/scan" replace />} />
          <Route path="/inventory/transparency" element={<WorkspaceLayout><TransparencyPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan" element={<WorkspaceLayout><CodeEntryPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/:itemId" element={<WorkspaceLayout><ScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/fixture/:fixtureId" element={<WorkspaceLayout><FixtureScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/asset/:assetId" element={<WorkspaceLayout><AssetScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/location/:locationId" element={<WorkspaceLayout><LocationScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/donation-item/:itemId" element={<WorkspaceLayout><DonationItemScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/locations" element={<WorkspaceLayout><LocationListPage /></WorkspaceLayout>} />
          <Route path="/inventory/locations/new" element={<WorkspaceLayout><LocationFormPage /></WorkspaceLayout>} />
          <Route path="/inventory/locations/:id" element={<WorkspaceLayout><LocationDetailPage /></WorkspaceLayout>} />
          <Route path="/inventory/locations/:id/reconcile" element={<WorkspaceLayout><InventoryReconciliationPage /></WorkspaceLayout>} />
          <Route path="/inventory/locations/:id/edit" element={<WorkspaceLayout><LocationFormPage /></WorkspaceLayout>} />
          <Route path="/inventory/categories" element={<WorkspaceLayout><CategoryListPage /></WorkspaceLayout>} />
          <Route path="/inventory/categories/new" element={<WorkspaceLayout><CategoryFormPage /></WorkspaceLayout>} />
          <Route path="/inventory/categories/:id/edit" element={<WorkspaceLayout><CategoryFormPage /></WorkspaceLayout>} />

          {/* Purchasing Workspace */}
          <Route path="/purchasing/orders" element={<WorkspaceLayout><PurchaseOrderListPage /></WorkspaceLayout>} />
          <Route path="/purchasing/orders/new" element={<WorkspaceLayout><PurchaseOrderFormPage /></WorkspaceLayout>} />
          <Route path="/purchasing/orders/:orderId" element={<WorkspaceLayout><PurchaseOrderPage /></WorkspaceLayout>} />

          {/* Assets Workspace */}
          <Route path="/assets" element={<WorkspaceLayout><AssetsPage /></WorkspaceLayout>} />
          <Route path="/assets/new" element={<WorkspaceLayout><AssetFormPage /></WorkspaceLayout>} />
          <Route path="/assets/:id" element={<WorkspaceLayout><AssetDetailPage /></WorkspaceLayout>} />
          <Route path="/assets/:id/edit" element={<WorkspaceLayout><AssetFormPage /></WorkspaceLayout>} />
          <Route path="/assets/:assetId/maintenance/new" element={<WorkspaceLayout><MaintenanceItemFormPage /></WorkspaceLayout>} />
          <Route path="/assets/:assetId/maintenance/:id/edit" element={<WorkspaceLayout><MaintenanceItemFormPage /></WorkspaceLayout>} />

          {/* Facilities Workspace */}
          <Route path="/facilities/tv-dashboard" element={<TVDashboard />} />
          <Route path="/facilities/tv-dashboard/:location" element={<TVDashboard />} />
          <Route path="/facilities/screens" element={<WorkspaceLayout><ScreensListPage /></WorkspaceLayout>} />
          <Route path="/facilities/screens/:slug" element={<WorkspaceLayout><ScreenEditPage /></WorkspaceLayout>} />
          <Route path="/kiosk/:slug" element={<KioskDisplayPage />} />
          <Route path="/facilities/logistics" element={<LogisticsDashboard />} />
          <Route path="/facilities/maker-boxes/scan" element={<WorkspaceLayout><MakerBoxScanPage /></WorkspaceLayout>} />
          <Route path="/facilities/maker-boxes" element={<WorkspaceLayout><MakerBoxAdminPage /></WorkspaceLayout>} />

          {/* Preventive Maintenance */}
          <Route path="/maintenance/dashboard" element={<WorkspaceLayout><MaintenanceDashboard /></WorkspaceLayout>} />
          <Route path="/maintenance/work-orders/:id" element={<WorkspaceLayout><WorkOrderPage /></WorkspaceLayout>} />
          <Route path="/facilities/checklist/:checklistId/complete/:completionId" element={<WorkspaceLayout><ChecklistCompletionPage /></WorkspaceLayout>} />

          {/* SIGs Workspace */}
          <Route path="/sigs" element={<Navigate to="/sigs/dashboard" replace />} />
          <Route path="/sigs/dashboard" element={<WorkspaceLayout><SIGDashboard /></WorkspaceLayout>} />
          <Route path="/sigs/dashboard/:sigId" element={<WorkspaceLayout><SIGDashboard /></WorkspaceLayout>} />

          {/* Reports Workspace */}
          <Route path="/reports/inventory" element={<WorkspaceLayout><InventoryReportPage /></WorkspaceLayout>} />
          <Route path="/reports/purchasing" element={<WorkspaceLayout><PurchasingReportPage /></WorkspaceLayout>} />
          <Route path="/reports/assets" element={<WorkspaceLayout><AssetReportPage /></WorkspaceLayout>} />

          {/* Settings Workspace */}
          <Route path="/settings/profile" element={<WorkspaceLayout><UserProfilePage /></WorkspaceLayout>} />
          <Route path="/settings/site" element={<WorkspaceLayout><SiteSettingsPage /></WorkspaceLayout>} />
          <Route path="/settings/tax-receipt/lookup" element={<WorkspaceLayout><TaxReceiptLookupPage /></WorkspaceLayout>} />
          <Route path="/settings/webhooks" element={<WorkspaceLayout><WebhookListPage /></WorkspaceLayout>} />
          <Route path="/settings/webhooks/new" element={<WorkspaceLayout><WebhookFormPage /></WorkspaceLayout>} />
          <Route path="/settings/webhooks/:id" element={<WorkspaceLayout><WebhookDetailPage /></WorkspaceLayout>} />
          <Route path="/settings/webhooks/:id/edit" element={<WorkspaceLayout><WebhookFormPage /></WorkspaceLayout>} />

          {/* Legacy routes - redirect to new workspace routes */}
          <Route path="/orderadmin" element={<Navigate to="/inventory/admin" replace />} />
          <Route path="/code-entry" element={<Navigate to="/inventory/scan" replace />} />
          <Route path="/transparency" element={<Navigate to="/inventory/transparency" replace />} />
          <Route path="/purchase-order" element={<Navigate to="/purchasing/orders" replace />} />
          <Route path="/purchase-order/:orderId" element={<RedirectPurchaseOrder />} />
          <Route path="/sig-dashboard" element={<Navigate to="/sigs/dashboard" replace />} />
          <Route path="/sig-dashboard/:sigId" element={<RedirectSIGDashboard />} />
          <Route path="/tax-receipt/lookup" element={<Navigate to="/settings/tax-receipt/lookup" replace />} />
          <Route path="/scan/:itemId" element={<RedirectScanItem />} />
          <Route path="/scan/fixture/:fixtureId" element={<RedirectScanFixture />} />
          <Route path="/scan/asset/:assetId" element={<RedirectScanAsset />} />
          <Route path="/scan/location/:locationId" element={<RedirectScanLocation />} />
          <Route path="/scan/donation-item/:itemId" element={<RedirectScanDonationItem />} />
          <Route path="/checklist/:checklistId/complete/:completionId" element={<RedirectChecklist />} />

          {/* Other routes */}
          <Route path="/thanks" element={<WorkspaceLayout><ThanksPage /></WorkspaceLayout>} />
        </SentryRoutes>
      </Sentry.ErrorBoundary>
    </div>
  );
}

function App() {
  return (
    <Router>
      <AppContent />
    </Router>
  );
}

export default App;
