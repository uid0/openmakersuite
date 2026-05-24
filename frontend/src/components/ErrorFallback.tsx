/**
 * Error Fallback Component
 * Displays an error and offers retry / go-home actions.
 */
import React from 'react';
import './ErrorFallback.css';

interface ErrorFallbackProps {
  error: Error;
  resetError: () => void;
}

const ErrorFallback: React.FC<ErrorFallbackProps> = ({ error, resetError }) => {
  React.useEffect(() => {
    // eslint-disable-next-line no-console
    console.error('Error caught by ErrorFallback:', error);
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
