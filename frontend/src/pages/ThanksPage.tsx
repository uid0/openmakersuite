/**
 * Thanks Page
 * Simple thank you page for non-authenticated users who submit reorder requests
 */
import React, { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import '../styles/ThanksPage.css';

const ThanksPage: React.FC = () => {
  const navigate = useNavigate();

  useEffect(() => {
    // Auto redirect to home after 5 seconds
    const timer = setTimeout(() => {
      navigate('/');
    }, 5000);

    return () => clearTimeout(timer);
  }, [navigate]);

  return (
    <div className="thanks-page">
      <div className="thanks-content">
        <div className="success-icon">✓</div>
        <h1>Thanks for letting us know!</h1>
        <p>
          Your reorder request has been submitted successfully.
        </p>
        <p>
          Our inventory team will review your request and take appropriate action.
        </p>
        <div className="actions">
          <button 
            onClick={() => navigate('/')}
            className="btn-primary"
          >
            Back to Home
          </button>
        </div>
        <p className="redirect-message">
          Automatically redirecting to home in a few seconds...
        </p>
      </div>
    </div>
  );
};

export default ThanksPage;
