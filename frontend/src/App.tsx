/**
 * Main App Component
 */
import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import * as Sentry from '@sentry/react';
import HomePage from './pages/HomePage';
import ScanPage from './pages/ScanPage';
import AdminDashboard from './pages/AdminDashboard';
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
            <Route path="/admin" element={<AdminDashboard />} />
          </SentryRoutes>
        </Sentry.ErrorBoundary>
      </div>
    </Router>
  );
}

export default App;
