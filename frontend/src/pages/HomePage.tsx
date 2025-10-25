/**
 * Home Page
 * Landing page with links to scan items and admin dashboard
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import '../styles/HomePage.css';

const HomePage: React.FC = () => {
  const navigate = useNavigate();

  return (
    <div className="home-page">
      <div className="hero">
        <h1>Makerspace Inventory Management</h1>
        <p className="tagline">Scan. Track. Reorder. Simple.</p>
      </div>

      <div className="card-grid">
        <div className="feature-card">
          <div className="icon">📱</div>
          <h2>Scan QR Code</h2>
          <p>
            Scan the QR code on any item shelf label to view details and submit
            reorder requests.
          </p>
          <p className="instruction">
            Use your phone camera to scan the QR codes on inventory labels.
          </p>
        </div>

        <div className="feature-card" onClick={() => navigate('/admin')}>
          <div className="icon">⚙️</div>
          <h2>Admin Dashboard</h2>
          <p>
            Manage reorder queue, approve requests, and process bulk orders by
            supplier.
          </p>
          <button className="card-button">Go to Dashboard</button>
        </div>

        <div className="feature-card">
          <div className="icon">📊</div>
          <h2>Features</h2>
          <ul className="feature-list">
            <li>Automatic QR code generation</li>
            <li>3x5" printable index cards</li>
            <li>Lead time tracking</li>
            <li>Multi-supplier support</li>
            <li>Stock level monitoring</li>
          </ul>
        </div>
      </div>

      <div className="info-section">
        <h2>How It Works</h2>
        <div className="steps">
          <div className="step">
            <div className="step-number">1</div>
            <h3>Create Item</h3>
            <p>
              Add inventory items via Django admin with photos, descriptions,
              and reorder quantities.
            </p>
          </div>
          <div className="step">
            <div className="step-number">2</div>
            <h3>Generate Labels</h3>
            <p>
              Print 3x5" index cards with QR codes to laminate and hang from
              shelves.
            </p>
          </div>
          <div className="step">
            <div className="step-number">3</div>
            <h3>Scan & Request</h3>
            <p>
              Users scan QR codes when items run low and submit reorder
              requests.
            </p>
          </div>
          <div className="step">
            <div className="step-number">4</div>
            <h3>Admin Review</h3>
            <p>
              Admins review requests, generate supplier cart links, and track
              deliveries.
            </p>
          </div>
        </div>
      </div>

      <footer className="footer">
        <p>
          Open Source Makerspace Inventory Management System
          <br />
          <a
            href="https://github.com/yourusername/makerspace-inventory"
            target="_blank"
            rel="noopener noreferrer"
          >
            View on GitHub
          </a>
        </p>
      </footer>
    </div>
  );
};

export default HomePage;
