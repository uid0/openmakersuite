/**
 * Authentication Section Component
 * Simple login/register for makerspace users
 */
import React, { useEffect, useState } from 'react';
import { authAPI } from '../services/api';
import '../styles/AuthSection.css';

interface AuthSectionProps {
  onAuthChange: (isLoggedIn: boolean, username?: string) => void;
}

const AuthSection: React.FC<AuthSectionProps> = ({ onAuthChange }) => {
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(false);
  const [username, setUsername] = useState<string>('');
  const [showLoginForm, setShowLoginForm] = useState<boolean>(false);
  const [showRegisterForm, setShowRegisterForm] = useState<boolean>(false);
  
  // Form states
  const [loginUsername, setLoginUsername] = useState<string>('');
  const [loginPassword, setLoginPassword] = useState<string>('');
  const [registerUsername, setRegisterUsername] = useState<string>('');
  const [registerEmail, setRegisterEmail] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');

  useEffect(() => {
    // Check if user is already logged in
    const token = localStorage.getItem('token');
    const savedUsername = localStorage.getItem('username');
    if (token && savedUsername) {
      setIsLoggedIn(true);
      setUsername(savedUsername);
      onAuthChange(true, savedUsername);
    }
  }, [onAuthChange]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const response = await authAPI.login(loginUsername, loginPassword);
      
      localStorage.setItem('token', response.data.access);
      localStorage.setItem('refresh_token', response.data.refresh);
      localStorage.setItem('username', loginUsername);
      
      setIsLoggedIn(true);
      setUsername(loginUsername);
      setShowLoginForm(false);
      setLoginUsername('');
      setLoginPassword('');
      onAuthChange(true, loginUsername);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Login failed. Please check your credentials.');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      // Register new user with simple password
      const password = 'makerspace123'; // Simple default password for makerspace
      
      await authAPI.register({
        username: registerUsername,
        email: registerEmail,
        password: password,
        password2: password,
      });

      // Auto-login after registration
      const loginResponse = await authAPI.login(registerUsername, password);
      
      localStorage.setItem('token', loginResponse.data.access);
      localStorage.setItem('refresh_token', loginResponse.data.refresh);
      localStorage.setItem('username', registerUsername);
      
      setIsLoggedIn(true);
      setUsername(registerUsername);
      setShowRegisterForm(false);
      setRegisterUsername('');
      setRegisterEmail('');
      onAuthChange(true, registerUsername);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Registration failed. Username may already exist.');
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('username');
    setIsLoggedIn(false);
    setUsername('');
    onAuthChange(false);
  };

  const resetForms = () => {
    setShowLoginForm(false);
    setShowRegisterForm(false);
    setError('');
    setLoginUsername('');
    setLoginPassword('');
    setRegisterUsername('');
    setRegisterEmail('');
  };

  if (isLoggedIn) {
    return (
      <div className="auth-section logged-in">
        <div className="user-info">
          <div className="user-avatar">👤</div>
          <div className="user-details">
            <span className="welcome">Welcome back,</span>
            <span className="username">{username}!</span>
          </div>
          <button onClick={handleLogout} className="logout-btn">
            Logout
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-section">
      <div className="auth-header">
        <h3>🔓 Quick Access</h3>
        <p>Login for enhanced features like supplier selection and order history</p>
      </div>

      {!showLoginForm && !showRegisterForm && (
        <div className="auth-buttons">
          <button 
            onClick={() => setShowLoginForm(true)}
            className="btn-primary"
          >
            Login
          </button>
          <button 
            onClick={() => setShowRegisterForm(true)}
            className="btn-secondary"
          >
            Quick Register
          </button>
        </div>
      )}

      {error && (
        <div className="error-message">
          {error}
        </div>
      )}

      {showLoginForm && (
        <form onSubmit={handleLogin} className="auth-form">
          <h4>Login to Your Account</h4>
          <div className="form-group">
            <input
              type="text"
              placeholder="Username"
              value={loginUsername}
              onChange={(e) => setLoginUsername(e.target.value)}
              required
            />
          </div>
          <div className="form-group">
            <input
              type="password"
              placeholder="Password"
              value={loginPassword}
              onChange={(e) => setLoginPassword(e.target.value)}
              required
            />
          </div>
          <div className="form-actions">
            <button type="submit" disabled={loading} className="btn-primary">
              {loading ? 'Logging in...' : 'Login'}
            </button>
            <button type="button" onClick={resetForms} className="btn-cancel">
              Cancel
            </button>
          </div>
        </form>
      )}

      {showRegisterForm && (
        <form onSubmit={handleRegister} className="auth-form">
          <h4>Quick Register</h4>
          <p className="register-info">
            🔧 Simple registration for makerspace members. 
            We'll use a default secure password - just remember your username!
          </p>
          <div className="form-group">
            <input
              type="text"
              placeholder="Choose a username"
              value={registerUsername}
              onChange={(e) => setRegisterUsername(e.target.value)}
              required
            />
          </div>
          <div className="form-group">
            <input
              type="email"
              placeholder="Email (optional but recommended)"
              value={registerEmail}
              onChange={(e) => setRegisterEmail(e.target.value)}
            />
          </div>
          <div className="form-actions">
            <button type="submit" disabled={loading} className="btn-primary">
              {loading ? 'Creating Account...' : 'Create Account'}
            </button>
            <button type="button" onClick={resetForms} className="btn-cancel">
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
};

export default AuthSection;
