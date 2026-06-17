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
  const tryAgainRef = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    // eslint-disable-next-line no-console
    console.error('Error caught by ErrorFallback:', error);
  }, [error]);

  React.useEffect(() => {
    // Move focus to the primary recovery action so keyboard and screen-reader
    // users land on something actionable when the fallback replaces the page.
    tryAgainRef.current?.focus();
  }, []);

  return (
    <div className="error-fallback" role="alert">
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
          <button ref={tryAgainRef} onClick={resetError} className="btn-try-again">
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
