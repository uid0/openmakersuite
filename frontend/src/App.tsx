/**
 * Main App Component
 */
import * as Sentry from '@sentry/react';
import { Route, BrowserRouter as Router, Routes } from 'react-router-dom';
import AdminDashboard from './pages/AdminDashboard';
import AssetScanPage from './pages/AssetScanPage';
import ChecklistCompletionPage from './pages/ChecklistCompletionPage';
import CodeEntryPage from './pages/CodeEntryPage';
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
import TVDashboard from './pages/TVDashboard';
import './styles/App.css';

// Wrap routes with Sentry for better error tracking
const SentryRoutes = Sentry.withSentryRouting(Routes);

function App() {
  return (
    <Router>
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
            <Route path="/" element={<HomePage />} />
            <Route path="/scan/:itemId" element={<ScanPage />} />
            <Route path="/scan/fixture/:fixtureId" element={<FixtureScanPage />} />
            <Route path="/scan/asset/:assetId" element={<AssetScanPage />} />
            <Route path="/scan/location/:locationId" element={<LocationScanPage />} />
            <Route path="/checklist/:checklistId/complete/:completionId" element={<ChecklistCompletionPage />} />
            <Route path="/code-entry" element={<CodeEntryPage />} />
            <Route path="/orderadmin" element={<AdminDashboard />} />
            <Route path="/thanks" element={<ThanksPage />} />
            <Route path="/tv-dashboard" element={<TVDashboard />} />
            <Route path="/tv-dashboard/:location" element={<TVDashboard />} />
            <Route path="/tv-logistics" element={<LogisticsDashboard />} />
            <Route path="/transparency" element={<TransparencyPage />} />
            <Route path="/purchase-order" element={<PurchaseOrderListPage />} />
            <Route path="/purchase-order/:orderId" element={<PurchaseOrderPage />} />
            <Route path="/sig-dashboard" element={<SIGDashboard />} />
            <Route path="/sig-dashboard/:sigId" element={<SIGDashboard />} />
          </SentryRoutes>
        </Sentry.ErrorBoundary>
      </div>
    </Router>
  );
}

export default App;
