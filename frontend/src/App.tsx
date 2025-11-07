/**
 * Main App Component
 */
import * as Sentry from '@sentry/react';
import { Route, BrowserRouter as Router, Routes } from 'react-router-dom';
import AdminDashboard from './pages/AdminDashboard';
import AssetScanPage from './pages/AssetScanPage';
import FixtureScanPage from './pages/FixtureScanPage';
import HomePage from './pages/HomePage';
import LogisticsDashboard from './pages/LogisticsDashboard';
import ScanPage from './pages/ScanPage';
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
            <Route path="/orderadmin" element={<AdminDashboard />} />
            <Route path="/thanks" element={<ThanksPage />} />
            <Route path="/tv-dashboard" element={<TVDashboard />} />
            <Route path="/tv-dashboard/:location" element={<TVDashboard />} />
            <Route path="/tv-logistics" element={<LogisticsDashboard />} />
            <Route path="/transparency" element={<TransparencyPage />} />
          </SentryRoutes>
        </Sentry.ErrorBoundary>
      </div>
    </Router>
  );
}

export default App;
