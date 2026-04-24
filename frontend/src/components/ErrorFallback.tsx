/**
 * Error Fallback Component with Sentry User Feedback
 * Displays error information and allows users to submit feedback
 */
import * as Sentry from '@sentry/react';
import React, { useState } from 'react';
import { showError } from '../utils/dialogs';
import './ErrorFallback.css';

interface ErrorFallbackProps {
  error: Error;
  resetError: () => void;
  eventId?: string;
}

const ErrorFallback: React.FC<ErrorFallbackProps> = ({ error, resetError, eventId }) => {
  const [feedback, setFeedback] = useState('');
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [capturedEventId, setCapturedEventId] = useState<string | undefined>(eventId);

  // Capture the error if we don't have an eventId yet
  React.useEffect(() => {
    if (!capturedEventId) {
      const id = Sentry.captureException(error);
      setCapturedEventId(id);
    }
  }, [error, capturedEventId]);

  const handleSubmitFeedback = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!feedback.trim()) {
      showError('Please tell us what you were doing when this error occurred.');
      return;
    }

    const feedbackEventId = capturedEventId || eventId;
    
    if (!feedbackEventId) {
      // If we still don't have an eventId, capture the error with context
      const id = Sentry.captureException(error, {
        contexts: {
          user_feedback: {
            feedback: feedback,
            email: email || undefined,
          },
        },
      });
      
      // Submit feedback with the newly captured eventId
      if (id) {
        try {
          Sentry.captureUserFeedback({
            event_id: id,
            name: email || 'Anonymous User',
            email: email || undefined,
            comments: feedback,
          });
        } catch (err) {
          console.error('Error submitting feedback:', err);
        }
      }
      
      setSubmitted(true);
      setTimeout(() => {
        setFeedback('');
        setEmail('');
        setSubmitted(false);
      }, 3000);
      return;
    }

    try {
      setSubmitting(true);
      
      // Submit user feedback to Sentry
      Sentry.captureUserFeedback({
        event_id: feedbackEventId,
        name: email || 'Anonymous User',
        email: email || undefined,
        comments: feedback,
      });

      setSubmitted(true);
      setTimeout(() => {
        setFeedback('');
        setEmail('');
        setSubmitted(false);
      }, 3000);
    } catch (err) {
      console.error('Error submitting feedback:', err);
      showError('Failed to submit feedback. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

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

        {!submitted ? (
          <form onSubmit={handleSubmitFeedback} className="feedback-form">
            <h2>Help us fix this issue</h2>
            <p className="feedback-prompt">
              Please tell us what you were doing when this error occurred. This will help us fix the problem.
            </p>
            
            <div className="form-group">
              <label htmlFor="feedback">What were you doing? *</label>
              <textarea
                id="feedback"
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="Describe what you were doing when the error occurred..."
                rows={4}
                required
                disabled={submitting}
              />
            </div>

            <div className="form-group">
              <label htmlFor="email">Your email (optional)</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your.email@example.com"
                disabled={submitting}
              />
              <small>We'll only use this to contact you if we need more information about this error.</small>
            </div>

            <button 
              type="submit" 
              className="btn-submit-feedback"
              disabled={submitting || !feedback.trim()}
            >
              {submitting ? 'Submitting...' : 'Submit Feedback'}
            </button>
          </form>
        ) : (
          <div className="feedback-success">
            <p>✓ Thank you for your feedback! We've received your report and will investigate this issue.</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default ErrorFallback;

