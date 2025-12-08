/**
 * Main App Component
 */
import * as Sentry from '@sentry/react';
import { Navigate, Route, BrowserRouter as Router, Routes, useParams } from 'react-router-dom';
import AdminDashboard from './pages/AdminDashboard';
import AssetScanPage from './pages/AssetScanPage';
import AssetsPage from './pages/AssetsPage';
import ChecklistCompletionPage from './pages/ChecklistCompletionPage';
import CodeEntryPage from './pages/CodeEntryPage';
import DonationItemScanPage from './pages/DonationItemScanPage';
import FixtureScanPage from './pages/FixtureScanPage';
import HomePage from './pages/HomePage';
import LocationScanPage from './pages/LocationScanPage';
import LogisticsDashboard from './pages/LogisticsDashboard';
import PurchaseOrderListPage from './pages/PurchaseOrderListPage';
import PurchaseOrderPage from './pages/PurchaseOrderPage';
import ScanPage from './pages/ScanPage';
import SIGDashboard from './pages/SIGDashboard';
import ThanksPage from './pages/ThanksPage';
import TransparencyPage from './pages/TransparencyPage';
import TaxReceiptLookupPage from './pages/TaxReceiptLookupPage';
import TVDashboard from './pages/TVDashboard';
import WorkspaceLayout from './components/WorkspaceLayout';
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
        fallback={({ error, resetError }) => (
          <div style={{ padding: '20px', textAlign: 'center' }}>
            <h1>Something went wrong</h1>
            <p>{error.message}</p>
            <button onClick={resetError}>Try again</button>
          </div>
        )}
        showDialog
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
          <Route path="/inventory/assets" element={<WorkspaceLayout><AssetsPage /></WorkspaceLayout>} />
          <Route path="/inventory/admin" element={<WorkspaceLayout><AdminDashboard /></WorkspaceLayout>} />
          <Route path="/inventory/code-entry" element={<WorkspaceLayout><CodeEntryPage /></WorkspaceLayout>} />
          <Route path="/inventory/transparency" element={<WorkspaceLayout><TransparencyPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/:itemId" element={<WorkspaceLayout><ScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/fixture/:fixtureId" element={<WorkspaceLayout><FixtureScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/asset/:assetId" element={<WorkspaceLayout><AssetScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/location/:locationId" element={<WorkspaceLayout><LocationScanPage /></WorkspaceLayout>} />
          <Route path="/inventory/scan/donation-item/:itemId" element={<WorkspaceLayout><DonationItemScanPage /></WorkspaceLayout>} />

          {/* Purchasing Workspace */}
          <Route path="/purchasing/orders" element={<WorkspaceLayout><PurchaseOrderListPage /></WorkspaceLayout>} />
          <Route path="/purchasing/orders/:orderId" element={<WorkspaceLayout><PurchaseOrderPage /></WorkspaceLayout>} />

          {/* Assets Workspace */}
          <Route path="/assets" element={<WorkspaceLayout><AssetsPage /></WorkspaceLayout>} />

          {/* Facilities Workspace */}
          <Route path="/facilities/tv-dashboard" element={<TVDashboard />} />
          <Route path="/facilities/tv-dashboard/:location" element={<TVDashboard />} />
          <Route path="/facilities/logistics" element={<LogisticsDashboard />} />
          <Route path="/facilities/checklist/:checklistId/complete/:completionId" element={<WorkspaceLayout><ChecklistCompletionPage /></WorkspaceLayout>} />

          {/* SIGs Workspace */}
          <Route path="/sigs/dashboard" element={<WorkspaceLayout><SIGDashboard /></WorkspaceLayout>} />
          <Route path="/sigs/dashboard/:sigId" element={<WorkspaceLayout><SIGDashboard /></WorkspaceLayout>} />

          {/* Settings Workspace */}
          <Route path="/settings/tax-receipt/lookup" element={<WorkspaceLayout><TaxReceiptLookupPage /></WorkspaceLayout>} />

          {/* Legacy routes - redirect to new workspace routes */}
          <Route path="/orderadmin" element={<Navigate to="/inventory/admin" replace />} />
          <Route path="/code-entry" element={<Navigate to="/inventory/code-entry" replace />} />
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
