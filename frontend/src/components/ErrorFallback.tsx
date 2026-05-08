/**
 * Error Fallback Component
 * Displays an error and offers retry / go-home actions. Errors caught by the
 * Highlight ErrorBoundary are reported to the dashboard automatically; this
 * component only re-fires the report on remount as a safety net for cases
 * where the boundary fired before Highlight initialized.
 */
import { H } from 'highlight.run';
import React from 'react';
import { redactError } from '../utils/redact';
import './ErrorFallback.css';

interface ErrorFallbackProps {
  error: Error;
  resetError: () => void;
}

const ErrorFallback: React.FC<ErrorFallbackProps> = ({ error, resetError }) => {
  React.useEffect(() => {
    // gh #378: scrub the error message + stack before they reach Highlight.
    // Errors thrown deep in the network code occasionally include the
    // failed request URL (which can carry an embedded signing key) or
    // an upstream's quoted Authorization header in the message string.
    H.consumeError(redactError(error), 'ErrorFallback');
  }, [error]);

  return (
    <div className="error-fallback">
      <div className="error-fallback-content">
        <h1>Something went wrong</h1>
        <p className="error-message">
          We're sorry, but something unexpected happened. Our team has been notified.
        </p>

        {process.env.NODE_ENV === 'development' && (
          <details className="error-details">
            <summary>Error Details (Development Only)</summary>
            <pre>{error.message}</pre>
            {error.stack && <pre>{error.stack}</pre>}
          </details>
        )}

        <div className="error-actions">
          <button onClick={resetError} className="btn-try-again">
            Try Again
          </button>
          <button onClick={() => window.location.href = '/'} className="btn-go-home">
            Go Home
          </button>
        </div>
      </div>
    </div>
  );
};

export default ErrorFallback;
