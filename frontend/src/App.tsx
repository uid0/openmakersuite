/**
 * Main App Component
 */
import * as Sentry from '@sentry/react';
import React from 'react';
import { Navigate, Route, BrowserRouter as Router, Routes, useParams } from 'react-router-dom';
import ErrorFallback from './components/ErrorFallback';
import WorkspaceLayout from './components/WorkspaceLayout';
import AdminDashboard from './pages/AdminDashboard';
import AuditFeedPage from './pages/AuditFeedPage';
import AnalyticsDashboardPage from './pages/AnalyticsDashboardPage';
import AssetDetailPage from './pages/AssetDetailPage';
import AssetFormPage from './pages/AssetFormPage';
import MaintenanceItemFormPage from './pages/MaintenanceItemFormPage';
import AssetReportPage from './pages/AssetReportPage';
import AssetScanPage from './pages/AssetScanPage';
import AssetsPage from './pages/AssetsPage';
import BreakerFormPage from './pages/BreakerFormPage';
import BreakerTracePage from './pages/BreakerTracePage';
import CategoryFormPage from './pages/CategoryFormPage';
import CategoryListPage from './pages/CategoryListPage';
import ChecklistCompletionPage from './pages/ChecklistCompletionPage';
import CodeEntryPage from './pages/CodeEntryPage';
import DashboardPage from './pages/DashboardPage';
import DonationItemScanPage from './pages/DonationItemScanPage';
import AssetPowerChainPage from './pages/AssetPowerChainPage';
import BreakerTripImpactPage from './pages/BreakerTripImpactPage';
import CircuitLoadPage from './pages/CircuitLoadPage';
import ElectricalCircuitsPage from './pages/ElectricalCircuitsPage';
import ElectricalOverviewPage from './pages/ElectricalOverviewPage';
import FacilitiesChecklistsPage from './pages/FacilitiesChecklistsPage';
import FacilitiesOverviewPage from './pages/FacilitiesOverviewPage';
import FacilitiesProjectStorageListPage from './pages/FacilitiesProjectStorageListPage';
import FacilitiesProjectStoragePage from './pages/FacilitiesProjectStoragePage';
import PowerBreakerFormPage from './pages/PowerBreakerFormPage';
import PowerCircuitFormPage from './pages/PowerCircuitFormPage';
import PowerOutletFormPage from './pages/PowerOutletFormPage';
import PowerPanelDetailPage from './pages/PowerPanelDetailPage';
import PowerPanelPrintPage from './pages/PowerPanelPrintPage';
import PowerPanelDirectoryPage from './pages/PowerPanelDirectoryPage';
import PowerPanelFormPage from './pages/PowerPanelFormPage';
import FixtureScanPage from './pages/FixtureScanPage';
import ForgeKeyCertificatesPage from './pages/ForgeKeyCertificatesPage';
import ForgeKeyDashboardPage from './pages/ForgeKeyDashboardPage';
import ForgeKeyDeviceDetailPage from './pages/ForgeKeyDeviceDetailPage';
import ForgeKeyDevicesPage from './pages/ForgeKeyDevicesPage';
import ForgeKeyDeviceTypesPage from './pages/ForgeKeyDeviceTypesPage';
import ForgeKeyEPaperBindPage from './pages/ForgeKeyEPaperBindPage';
import ForgeKeyEPaperPanelsPage from './pages/ForgeKeyEPaperPanelsPage';
import ForgeKeyEPaperServicePage from './pages/ForgeKeyEPaperServicePage';
import ForgeKeyFirmwareRolloutsPage from './pages/ForgeKeyFirmwareRolloutsPage';
import HomePage from './pages/HomePage';
import LockersPage from './pages/LockersPage';
import PurchasingOverviewPage from './pages/PurchasingOverviewPage';
import ReportsOverviewPage from './pages/ReportsOverviewPage';
import SettingsOverviewPage from './pages/SettingsOverviewPage';
import LightSwitchFormPage from './pages/LightSwitchFormPage';
import LocationSafetySignPage from './pages/LocationSafetySignPage';
import LocationSafetySignPrintPage from './pages/LocationSafetySignPrintPage';
import ThermostatFormPage from './pages/ThermostatFormPage';
import InventoryItemDetailPage from './pages/InventoryItemDetailPage';
import InventoryItemFormPage from './pages/InventoryItemFormPage';
import InventoryListPage from './pages/InventoryListPage';
import InventoryOverviewPage from './pages/InventoryOverviewPage';
import InviteCodeAdminPage from './pages/InviteCodeAdminPage';
import InviteRedeemPage from './pages/InviteRedeemPage';
import RegisterWithTokenPage from './pages/RegisterWithTokenPage';
import InventoryReportPage from './pages/InventoryReportPage';
import InventoryReconciliationPage from './pages/InventoryReconciliationPage';
import LocationDetailPage from './pages/LocationDetailPage';
import LocationFormPage from './pages/LocationFormPage';
import LocationListPage from './pages/LocationListPage';
import LocationProblemDetailPage from './pages/LocationProblemDetailPage';
import LocationProblemsListPage from './pages/LocationProblemsListPage';
import LocationScanPage from './pages/LocationScanPage';
import LogisticsDashboard from './pages/LogisticsDashboard';
import MakerBoxAdminPage from './pages/MakerBoxAdminPage';
import MakerBoxPreConversionPage from './pages/MakerBoxPreConversionPage';
import MakerBoxScanPage from './pages/MakerBoxScanPage';
import MaintenanceDashboard from './pages/MaintenanceDashboard';
import MaintenanceDashboardPage from './pages/MaintenanceDashboardPage';
import NetworkDropFormPage from './pages/NetworkDropFormPage';
import OutletFormPage from './pages/OutletFormPage';
import ThirdPartyWorkOrderPage from './pages/ThirdPartyWorkOrderPage';
import WorkOrderPage from './pages/WorkOrderPage';
import PurchaseOrderFormPage from './pages/PurchaseOrderFormPage';
import ProjectStorageKioskPage from './pages/ProjectStorageKioskPage';
import PurchaseOrderListPage from './pages/PurchaseOrderListPage';
import PurchaseOrderPage from './pages/PurchaseOrderPage';
import PurchasingReportPage from './pages/PurchasingReportPage';
import ScanPage from './pages/ScanPage';
import SIGDashboard from './pages/SIGDashboard';
import SiteSettingsPage from './pages/SiteSettingsPage';
import StorageVisionCapturePage from './pages/StorageVisionCapturePage';
import StorageVisionReviewPage from './pages/StorageVisionReviewPage';
import StorageVisionSetupPage from './pages/StorageVisionSetupPage';
import SupplierDetailPage from './pages/SupplierDetailPage';
import SupplierFormPage from './pages/SupplierFormPage';
import SupplierListPage from './pages/SupplierListPage';
import TaxReceiptLookupPage from './pages/TaxReceiptLookupPage';
import ThanksPage from './pages/ThanksPage';
import TransparencyPage from './pages/TransparencyPage';
import UniversalScannerPage from './pages/UniversalScannerPage';
import KioskDisplayPage from './pages/KioskDisplayPage';
import ScreenEditPage from './pages/ScreenEditPage';
import ScreensListPage from './pages/ScreensListPage';
import TVDashboard from './pages/TVDashboard';
import UserProfilePage from './pages/UserProfilePage';
import WebhookDetailPage from './pages/WebhookDetailPage';
import WebhookFormPage from './pages/WebhookFormPage';
import WebhookListPage from './pages/WebhookListPage';
import './styles/App.css';

interface ErrorBoundaryProps {
  fallback: (params: { error: Error; resetError: () => void }) => React.ReactNode;
  children: React.ReactNode;
}

class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('Uncaught error in render tree:', error, info);
    // Report to Sentry with the React component stack as context.
    // No-op when Sentry.init wasn't called (VITE_SENTRY_DSN unset).
    Sentry.captureException(error, { contexts: { react: { componentStack: info.componentStack ?? '' } } });
  }

  resetError = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return this.props.fallback({ error: this.state.error, resetError: this.resetError });
    }
    return this.props.children;
  }
}

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

// Phone-camera scan target: a project-storage label QR encodes
// /scan/project-storage/<stint_id>. The warden detail page lives at
// /facilities/project-storage/<stint_id>, so this redirect bridges the
// two so the QR keeps a friendly "/scan/..." shape while the routing
// matches the rest of the warden surface.
const RedirectScanProjectStorageStint = () => {
  const { stintId } = useParams();
  return <Navigate to={`/facilities/project-storage/${stintId}`} replace />;
};

const RedirectChecklist = () => {
  const { checklistId, completionId } = useParams();
  return <Navigate to={`/facilities/checklist/${checklistId}/complete/${completionId}`} replace />;
};

function AppContent() {
  return (
    <div className="App">
      <ErrorBoundary
        fallback={({ error, resetError }) => (
          <ErrorFallback error={error} resetError={resetError} />
        )}
      >
        <Routes>
          {/* Home/Landing */}
          <Route path="/" element={<HomePage />} />
          <Route path="/scan" element={<WorkspaceLayout><UniversalScannerPage /></WorkspaceLayout>} />

          {/* Anonymous invite redemption: outside person creates their own account */}
          <Route path="/invite/:code" element={<InviteRedeemPage />} />
          <Route path="/register/:token" element={<RegisterWithTokenPage />} />

          {/* Staff: mint + revoke invite codes */}
          <Route path="/admin/invite-codes" element={<WorkspaceLayout><InviteCodeAdminPage /></WorkspaceLayout>} />
          <Route path="/admin/audit-feed" element={<WorkspaceLayout><AuditFeedPage /></WorkspaceLayout>} />

          {/* Public kiosks (no Django auth) */}
          <Route path="/project-storage/kiosk" element={<ProjectStorageKioskPage />} />

          {/* Redirects for backward compatibility */}
          <Route path="/tv-dashboard" element={<Navigate to="/facilities/tv-dashboard" replace />} />
          <Route path="/tv-dashboard/:location" element={<RedirectTVDashboardLocation />} />
          <Route path="/tv-logistics" element={<Navigate to="/facilities/logistics" replace />} />

          {/* Inventory Workspace */}
          <Route path="/inventory" element={<WorkspaceLayout><InventoryOverviewPage /></WorkspaceLayout>} />
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
          <Route path="/facilities/forgekey-dashboard" element={<WorkspaceLayout><ForgeKeyDashboardPage /></WorkspaceLayout>} />
          <Route path="/facilities/forgekey-devices" element={<WorkspaceLayout><ForgeKeyDevicesPage /></WorkspaceLayout>} />
          <Route path="/facilities/forgekey-devices/:id" element={<WorkspaceLayout><ForgeKeyDeviceDetailPage /></WorkspaceLayout>} />
          <Route path="/facilities/forgekey-epaper" element={<WorkspaceLayout><ForgeKeyEPaperPanelsPage /></WorkspaceLayout>} />
          <Route path="/facilities/storage-vision" element={<WorkspaceLayout><StorageVisionSetupPage /></WorkspaceLayout>} />
          <Route path="/facilities/storage-vision/capture" element={<WorkspaceLayout><StorageVisionCapturePage /></WorkspaceLayout>} />
          <Route path="/facilities/storage-vision/review" element={<WorkspaceLayout><StorageVisionReviewPage /></WorkspaceLayout>} />
          <Route path="/facilities/forgekey-rollouts" element={<WorkspaceLayout><ForgeKeyFirmwareRolloutsPage /></WorkspaceLayout>} />
          <Route path="/facilities/forgekey-certificates" element={<WorkspaceLayout><ForgeKeyCertificatesPage /></WorkspaceLayout>} />
          <Route path="/facilities/forgekey-device-types" element={<WorkspaceLayout><ForgeKeyDeviceTypesPage /></WorkspaceLayout>} />
          <Route path="/facilities/lockers" element={<WorkspaceLayout><LockersPage /></WorkspaceLayout>} />
          <Route path="/forgekey/epaper" element={<Navigate to="/facilities/forgekey-epaper" replace />} />
          <Route path="/forgekey/epaper/bind" element={<WorkspaceLayout><ForgeKeyEPaperBindPage /></WorkspaceLayout>} />
          <Route path="/forgekey/epaper/service" element={<WorkspaceLayout><ForgeKeyEPaperServicePage /></WorkspaceLayout>} />
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
          <Route path="/purchasing" element={<WorkspaceLayout><PurchasingOverviewPage /></WorkspaceLayout>} />
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
          <Route path="/facilities" element={<WorkspaceLayout><FacilitiesOverviewPage /></WorkspaceLayout>} />
          <Route path="/facilities/tv-dashboard" element={<TVDashboard />} />
          <Route path="/facilities/tv-dashboard/:location" element={<TVDashboard />} />
          <Route path="/facilities/screens" element={<WorkspaceLayout><ScreensListPage /></WorkspaceLayout>} />
          <Route path="/facilities/screens/:slug" element={<WorkspaceLayout><ScreenEditPage /></WorkspaceLayout>} />
          <Route path="/kiosk/:slug" element={<KioskDisplayPage />} />
          <Route path="/facilities/logistics" element={<LogisticsDashboard />} />
          <Route path="/facilities/maker-boxes/pre-conversion" element={<WorkspaceLayout><MakerBoxPreConversionPage /></WorkspaceLayout>} />
          <Route path="/facilities/maker-boxes/scan" element={<WorkspaceLayout><MakerBoxScanPage /></WorkspaceLayout>} />
          <Route path="/facilities/maker-boxes" element={<WorkspaceLayout><MakerBoxAdminPage /></WorkspaceLayout>} />

          {/* Electrical workspace — overview + power-topology visualization (oms-b25 [6/7]) */}
          <Route path="/facilities/electrical" element={<WorkspaceLayout><ElectricalOverviewPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/panels" element={<WorkspaceLayout><PowerPanelDirectoryPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/panels/new" element={<WorkspaceLayout><PowerPanelFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/panels/:id" element={<WorkspaceLayout><PowerPanelDetailPage /></WorkspaceLayout>} />
          {/* Print directory route renders bare — no workspace chrome — so the
              browser print dialog emits a clean letter-size cabinet directory. */}
          <Route path="/facilities/electrical/panels/:id/print" element={<PowerPanelPrintPage />} />
          <Route path="/facilities/electrical/panels/:id/edit" element={<WorkspaceLayout><PowerPanelFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/panels/:panelId/breakers/new" element={<WorkspaceLayout><PowerBreakerFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/breakers/:id/edit" element={<WorkspaceLayout><PowerBreakerFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/breakers/:id/trip-impact" element={<WorkspaceLayout><BreakerTripImpactPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/breakers/:breakerId/circuits/new" element={<WorkspaceLayout><PowerCircuitFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/circuits/:id/edit" element={<WorkspaceLayout><PowerCircuitFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/circuits/:id/load" element={<WorkspaceLayout><CircuitLoadPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/circuits/:circuitId/outlets/new" element={<WorkspaceLayout><PowerOutletFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/outlets/:id/edit" element={<WorkspaceLayout><PowerOutletFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/power-chain" element={<WorkspaceLayout><AssetPowerChainPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/power-chain/:assetId" element={<WorkspaceLayout><AssetPowerChainPage /></WorkspaceLayout>} />

          {/* Legacy fixture inventory (oms-a5f). Kept under /electrical/fixtures so
              existing bookmarks redirect cleanly via the routes below. */}
          <Route path="/facilities/electrical/fixtures" element={<WorkspaceLayout><ElectricalCircuitsPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/breakers/new" element={<WorkspaceLayout><BreakerFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/breakers/:id/edit" element={<WorkspaceLayout><BreakerFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/breakers/:id/trace" element={<WorkspaceLayout><BreakerTracePage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/outlets/new" element={<WorkspaceLayout><OutletFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/outlets/:id/edit" element={<WorkspaceLayout><OutletFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/light-switches/new" element={<WorkspaceLayout><LightSwitchFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/light-switches/:id/edit" element={<WorkspaceLayout><LightSwitchFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/network-drops/new" element={<WorkspaceLayout><NetworkDropFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/electrical/network-drops/:id/edit" element={<WorkspaceLayout><NetworkDropFormPage /></WorkspaceLayout>} />

          {/* Climate — thermostat CRUD (oms-gzycmj) */}
          <Route path="/facilities/climate/thermostats/new" element={<WorkspaceLayout><ThermostatFormPage /></WorkspaceLayout>} />
          <Route path="/facilities/climate/thermostats/:id/edit" element={<WorkspaceLayout><ThermostatFormPage /></WorkspaceLayout>} />

          {/* Per-location Safety Sign (oms-gzycmj). Print variant renders
              without the workspace shell so the browser print dialog
              produces a single clean sheet. */}
          <Route path="/facilities/locations/:id/safety-sign" element={<WorkspaceLayout><LocationSafetySignPage /></WorkspaceLayout>} />
          <Route path="/facilities/locations/:id/safety-sign/print" element={<LocationSafetySignPrintPage />} />

          {/* Analytics dashboard */}
          <Route path="/analytics" element={<WorkspaceLayout><AnalyticsDashboardPage /></WorkspaceLayout>} />
          <Route path="/analytics/shared" element={<AnalyticsDashboardPage shared />} />

          {/* Preventive Maintenance */}
          <Route path="/maintenance" element={<WorkspaceLayout><MaintenanceDashboardPage /></WorkspaceLayout>} />
          <Route path="/maintenance/dashboard" element={<WorkspaceLayout><MaintenanceDashboard /></WorkspaceLayout>} />
          <Route path="/maintenance/work-orders/:id" element={<WorkspaceLayout><WorkOrderPage /></WorkspaceLayout>} />
          <Route path="/maintenance/third-party/:id" element={<WorkspaceLayout><ThirdPartyWorkOrderPage /></WorkspaceLayout>} />
          <Route path="/maintenance/location-problems" element={<WorkspaceLayout><LocationProblemsListPage /></WorkspaceLayout>} />
          <Route path="/maintenance/location-problems/:id" element={<WorkspaceLayout><LocationProblemDetailPage /></WorkspaceLayout>} />
          <Route path="/facilities/checklist" element={<WorkspaceLayout><FacilitiesChecklistsPage /></WorkspaceLayout>} />
          <Route path="/facilities/checklist/:checklistId/complete/:completionId" element={<WorkspaceLayout><ChecklistCompletionPage /></WorkspaceLayout>} />
          <Route path="/facilities/project-storage/queue" element={<WorkspaceLayout><FacilitiesProjectStorageListPage /></WorkspaceLayout>} />
          <Route path="/facilities/project-storage/:stintId" element={<WorkspaceLayout><FacilitiesProjectStoragePage /></WorkspaceLayout>} />
          <Route path="/facilities/project-storage" element={<WorkspaceLayout><FacilitiesProjectStoragePage /></WorkspaceLayout>} />

          {/* SIGs Workspace */}
          <Route path="/sigs" element={<Navigate to="/sigs/dashboard" replace />} />
          <Route path="/sigs/dashboard" element={<WorkspaceLayout><SIGDashboard /></WorkspaceLayout>} />
          <Route path="/sigs/dashboard/:sigId" element={<WorkspaceLayout><SIGDashboard /></WorkspaceLayout>} />

          {/* Reports Workspace */}
          <Route path="/reports" element={<WorkspaceLayout><ReportsOverviewPage /></WorkspaceLayout>} />
          <Route path="/reports/inventory" element={<WorkspaceLayout><InventoryReportPage /></WorkspaceLayout>} />
          <Route path="/reports/purchasing" element={<WorkspaceLayout><PurchasingReportPage /></WorkspaceLayout>} />
          <Route path="/reports/assets" element={<WorkspaceLayout><AssetReportPage /></WorkspaceLayout>} />

          {/* Settings Workspace */}
          <Route path="/settings" element={<WorkspaceLayout><SettingsOverviewPage /></WorkspaceLayout>} />
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
          <Route path="/scan/project-storage/:stintId" element={<RedirectScanProjectStorageStint />} />
          <Route path="/checklist/:checklistId/complete/:completionId" element={<RedirectChecklist />} />

          {/* Other routes */}
          <Route path="/thanks" element={<WorkspaceLayout><ThanksPage /></WorkspaceLayout>} />
        </Routes>
      </ErrorBoundary>
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
