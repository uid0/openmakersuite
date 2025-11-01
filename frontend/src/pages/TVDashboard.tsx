/**
 * TV Dashboard Component - Optimized for Chromecast/TV display
 * Shows items that have been reordered and are in progress
 */
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { InventoryItem } from '../types';
import '../styles/TVDashboard.css';

// Create a dedicated API instance for TV Dashboard that doesn't send auth headers
const tvAPI = axios.create({
  baseURL: process.env.REACT_APP_API_URL || 'http://localhost:8000',
  headers: {
    'Content-Type': 'application/json',
  },
});

const TVDashboard: React.FC = () => {
  const [reorderedItems, setReorderedItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [currentMessageIndex, setCurrentMessageIndex] = useState(0);

  // Branding configuration - can be set via environment variables
  const [config] = useState({
    title: process.env.REACT_APP_DASHBOARD_TITLE || 'Dallas Makerspace Inventory',
    subtitle: process.env.REACT_APP_DASHBOARD_SUBTITLE || 'Items on Order',
    logo: process.env.REACT_APP_DASHBOARD_LOGO || null,
    showLogo: process.env.REACT_APP_SHOW_LOGO !== 'false',
    showTransparency: process.env.REACT_APP_SHOW_TRANSPARENCY !== 'false',
  });

  // Configurable footer messages - can be set via environment variables
  const footerMessages = useState(() => {
    const envMessages = process.env.REACT_APP_FOOTER_MESSAGES;
    if (envMessages) {
      try {
        return JSON.parse(envMessages);
      } catch (e) {
        console.warn('Invalid REACT_APP_FOOTER_MESSAGES format, using defaults');
      }
    }
    // Default messages
    return [
      'Tracking items from request to delivery',
      'Scan QR codes to request reorders',
      'Keeping your makerspace stocked',
      'Real-time inventory management',
      'Automated supply chain tracking'
    ];
  })[0];

  // Rotation interval - configurable via environment variable
  const rotationInterval = parseInt(process.env.REACT_APP_MESSAGE_ROTATION_SECONDS || '10') * 1000;

  const fetchReorderedItems = async () => {
    try {
      setError(null);
      // Use dedicated TV API instance that doesn't send auth headers
      const response = await tvAPI.get<InventoryItem[]>('/api/inventory/items/reordered/');
      setReorderedItems(response.data);
      setLastUpdated(new Date());
    } catch (err: any) {
      console.error('Error fetching reordered items:', err);
      console.error('Error details:', {
        status: err?.response?.status,
        statusText: err?.response?.statusText,
        data: err?.response?.data,
        code: err?.code,
        message: err?.message
      });
      
      if (err?.response?.status === 401) {
        setError('Authentication error - Dashboard requires public access');
      } else if (err?.response?.status >= 500) {
        setError('Backend server error - Check if the server is running');
      } else if (err?.code === 'ERR_NETWORK') {
        setError('Network error - Cannot connect to backend server');
      } else {
        setError(`Failed to load inventory data (${err?.response?.status || err?.code || 'Unknown error'})`);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Initial fetch
    fetchReorderedItems();

    // Set up auto-refresh every 30 seconds
    const interval = setInterval(fetchReorderedItems, 30000);

    return () => clearInterval(interval);
  }, []);

  // Message rotation effect
  useEffect(() => {
    if (footerMessages.length <= 1) return; // Don't rotate if only one message

    const interval = setInterval(() => {
      setCurrentMessageIndex((prevIndex) => 
        (prevIndex + 1) % footerMessages.length
      );
    }, rotationInterval);

    return () => clearInterval(interval);
  }, [footerMessages.length, rotationInterval]);

  const getOrderInfo = (item: InventoryItem) => {
    const request = item.active_reorder_request;
    return {
      quantity: request?.quantity || item.reorder_quantity,
      orderedQuantity: request?.quantity || 0,
      status: item.reorder_status
    };
  };

  const formatExpectedDelivery = (dateString: string) => {
    const date = new Date(dateString);
    const today = new Date();
    const diffTime = date.getTime() - today.getTime();
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    
    if (diffDays < 0) {
      return `Overdue by ${Math.abs(diffDays)} days`;
    } else if (diffDays === 0) {
      return 'Expected today';
    } else if (diffDays === 1) {
      return 'Expected tomorrow';
    } else {
      return `Expected in ${diffDays} days`;
    }
  };

  const getLastStatusInfo = (item: InventoryItem) => {
    const request = item.active_reorder_request;
    if (!request) return { status: 'Requested', date: null, by: null };

    switch (request.status) {
      case 'pending':
        return {
          status: 'Requested',
          date: request.requested_at,
          by: request.requested_by
        };
      case 'approved':
        return {
          status: 'Approved',
          date: request.reviewed_at || request.requested_at,
          by: request.reviewed_by || request.requested_by
        };
      case 'ordered':
        return {
          status: 'Ordered',
          date: request.ordered_at || request.reviewed_at || request.requested_at,
          by: request.reviewed_by || request.requested_by
        };
      default:
        return {
          status: 'Requested',
          date: request.requested_at,
          by: request.requested_by
        };
    }
  };

  const formatStatusDate = (dateString: string | null) => {
    if (!dateString) return '';
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { 
      month: 'short', 
      day: 'numeric',
      year: date.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined
    });
  };

  const getImageUrl = (imageUrl: string | null) => {
    if (!imageUrl) return '';
    // If URL is already absolute, return as-is
    if (imageUrl.startsWith('http://') || imageUrl.startsWith('https://')) {
      return imageUrl;
    }
    // If URL is relative, combine with base URL
    const baseUrl = process.env.REACT_APP_API_URL || 'http://localhost:8000';
    return `${baseUrl.replace(/\/$/, '')}${imageUrl}`;
  };


  const getOrderStatusClass = (status: string) => {
    switch (status) {
      case 'pending': return 'order-pending';
      case 'approved': return 'order-approved';
      case 'ordered': return 'order-placed';
      case 'cancelled': return 'order-cancelled';
      default: return 'order-default';
    }
  };

  const getProgressStages = (item: InventoryItem) => {
    const request = item.active_reorder_request;
    if (!request) return { currentStage: 0, stages: [], isCancelled: false };

    const stages = [
      { name: 'Requested', key: 'pending', completed: true },
      { name: 'Approved', key: 'approved', completed: false },
      { name: 'Ordered', key: 'ordered', completed: false },
      { name: 'En Route', key: 'en_route', completed: false },
      { name: 'Received', key: 'received', completed: false }
    ];

    let currentStage = 0;
    const isCancelled = request.status === 'cancelled';

    if (isCancelled) {
      return { currentStage: -1, stages, isCancelled: true };
    }

    // Mark completed stages based on current status
    switch (request.status) {
      case 'received':
        stages.forEach(stage => stage.completed = true);
        currentStage = 4;
        break;
      case 'ordered':
        // Check if we have delivery info to show "En Route"
        const hasDeliveryInfo = item.expected_delivery_date;
        if (hasDeliveryInfo) {
          stages[0].completed = true; // Requested
          stages[1].completed = true; // Approved  
          stages[2].completed = true; // Ordered
          stages[3].completed = false; // En Route (current)
          currentStage = 3;
        } else {
          stages[0].completed = true; // Requested
          stages[1].completed = true; // Approved
          stages[2].completed = false; // Ordered (current)
          currentStage = 2;
        }
        break;
      case 'approved':
        stages[0].completed = true; // Requested
        stages[1].completed = false; // Approved (current)
        currentStage = 1;
        break;
      case 'pending':
      default:
        stages[0].completed = false; // Requested (current)
        currentStage = 0;
        break;
    }

    return { currentStage, stages, isCancelled };
  };

  const formatLastUpdated = (date: Date) => {
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  };

  if (loading) {
    return (
      <div className="tv-dashboard">
        <div className="loading-screen">
          <div className="loading-spinner"></div>
          <h1>Loading Inventory Dashboard...</h1>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="tv-dashboard">
        <div className="error-screen">
          <div className="error-icon">⚠️</div>
          <h1>Connection Error</h1>
          <p>{error}</p>
          <p>Retrying automatically...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="tv-dashboard">
      <header className="dashboard-header">
        <div className="header-content">
          {config.showLogo && config.logo && (
            <div className="logo-container">
              <img src={config.logo} alt="Logo" className="dashboard-logo" />
            </div>
          )}
          <div className="title-section">
            <h1>
              <span className="icon">📦</span>
              {config.title}
            </h1>
            <h2 className="subtitle">{config.subtitle}</h2>
          </div>
        </div>
        <div className="status-bar">
          <span className="item-count">
            {reorderedItems.length} {reorderedItems.length === 1 ? 'Item' : 'Items'} on Order
          </span>
          <span className="last-updated">
            Last Updated: {formatLastUpdated(lastUpdated)}
          </span>
        </div>
      </header>

      <main className="dashboard-content">
        {reorderedItems.length === 0 ? (
          <div className="no-items">
            <div className="no-items-icon">✅</div>
            <h2>No Items on Order</h2>
            <p>All reorder requests have been completed</p>
          </div>
        ) : (
          <div className="items-grid">
            {reorderedItems.map((item) => {
              const orderInfo = getOrderInfo(item);
              return (
                <div key={item.id} className={`item-card ${getOrderStatusClass(orderInfo.status)}`}>
                  <div className="item-image">
                    {(item.image || item.thumbnail) ? (
                      <>
                        <div className="image-loading-placeholder">
                          <span>📦</span>
                        </div>
                        <img
                          src={getImageUrl(item.image) || getImageUrl(item.thumbnail)}
                          alt={item.name}
                          onLoad={(e) => {
                            // Hide loading placeholder when image loads
                            const parent = e.currentTarget.parentElement;
                            if (parent) {
                              const loading = parent.querySelector('.image-loading-placeholder');
                              if (loading) {
                                (loading as HTMLElement).style.display = 'none';
                              }
                            }
                          }}
                          onError={(e) => {
                            // If full image fails and we have a thumbnail, try thumbnail
                            const fullImageUrl = getImageUrl(item.image);
                            const thumbnailUrl = getImageUrl(item.thumbnail);
                            
                            if (fullImageUrl && thumbnailUrl && e.currentTarget.src === fullImageUrl) {
                              e.currentTarget.src = thumbnailUrl;
                            } else {
                              // If both fail or no alternatives, hide and show placeholder
                              const parent = e.currentTarget.parentElement;
                              if (parent) {
                                e.currentTarget.style.display = 'none';
                                const loading = parent.querySelector('.image-loading-placeholder');
                                if (loading) {
                                  (loading as HTMLElement).style.display = 'flex';
                                }
                                const placeholder = parent.querySelector('.placeholder-image-fallback');
                                if (placeholder) {
                                  (placeholder as HTMLElement).style.display = 'flex';
                                } else {
                                  const fallback = document.createElement('div');
                                  fallback.className = 'placeholder-image placeholder-image-fallback';
                                  fallback.innerHTML = '<span>📦</span>';
                                  fallback.style.display = 'flex';
                                  parent.appendChild(fallback);
                                }
                              }
                            }
                          }}
                        />
                        <div className="placeholder-image placeholder-image-fallback" style={{ display: 'none' }}>
                          <span>📦</span>
                        </div>
                      </>
                    ) : (
                      <div className="placeholder-image">
                        <span>📦</span>
                      </div>
                    )}
                  </div>

                  <div className="item-info">
                    <h3 className="item-name">{item.name}</h3>
                    <div className="item-details">
                      <div className="location">
                        <span className="icon">📍</span>
                        {item.location}
                      </div>
                      {item.category_name && (
                        <div className="category">
                          <span className="icon">🏷️</span>
                          {item.category_name}
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="order-info">
                    {/* Estimated Delivery Date (when available) */}
                    {item.expected_delivery_date && (
                      <div className="estimated-delivery-header">
                        <div className="estimated-label">Estimated</div>
                        <div className="estimated-date">
                          📅 {formatExpectedDelivery(item.expected_delivery_date)}
                        </div>
                      </div>
                    )}

                    {/* Progress Bar */}
                    {(() => {
                      const { currentStage, stages, isCancelled } = getProgressStages(item);
                      
                      if (isCancelled) {
                        return (
                          <div className="progress-cancelled">
                            <div className="cancelled-icon">❌</div>
                            <div className="cancelled-text">Cancelled</div>
                          </div>
                        );
                      }

                      const progressPercentage = currentStage > 0 ? (currentStage / (stages.length - 1)) * 100 : 0;
                      
                      return (
                        <div className="progress-container">
                          <div 
                            className="progress-bar"
                            style={{ 
                              '--progress-width': `${progressPercentage}%` 
                            } as React.CSSProperties & { '--progress-width': string }}
                          >
                            {stages.map((stage, index) => (
                              <div key={stage.key} className="progress-stage">
                                <div className={`stage-dot ${
                                  index < currentStage ? 'completed' : 
                                  index === currentStage ? 'current' : 'pending'
                                }`}>
                                  {index < currentStage ? '✓' : 
                                   index === currentStage ? '●' : '○'}
                                </div>
                                <div className={`stage-label ${
                                  index < currentStage ? 'completed' : 
                                  index === currentStage ? 'current' : 'pending'
                                }`}>
                                  {stage.name}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })()}

                    {/* Quantity and Status Info */}
                    <div className="order-details">
                      <div className="quantity-info">
                        {orderInfo.orderedQuantity || orderInfo.quantity} units
                      </div>
                      
                      <div className="status-history">
                        {(() => {
                          const statusInfo = getLastStatusInfo(item);
                          return (
                            <div className="last-status">
                              <div className="status-action">
                                <strong>{statusInfo.status}</strong>
                                {statusInfo.by && statusInfo.by.trim() !== '' && statusInfo.by !== 'Anonymous' && ` by ${statusInfo.by}`}
                              </div>
                              {statusInfo.date && (
                                <div className="status-date">
                                  {formatStatusDate(statusInfo.date)}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                      </div>
                    </div>
                  </div>

                </div>
              );
            })}
          </div>
        )}
      </main>

      <footer className="dashboard-footer">
        <div className="auto-refresh-indicator">
          <span className="refresh-icon">🔄</span>
          Auto-refreshing every 30 seconds
        </div>
        <div className="qr-instruction">
          <span className="qr-icon">📦</span>
          <span className="rotating-message">
            {footerMessages[currentMessageIndex]}
          </span>
        </div>
        <div className="footer-links">
          {config.showTransparency && (
            <div className="transparency-link">
              <span className="transparency-icon">🔍</span>
              <a 
                href="/transparency" 
                target="_blank" 
                rel="noopener noreferrer"
                className="transparency-link-text"
              >
                Financial Transparency
              </a>
            </div>
          )}
          <div className="debug-info" style={{ fontSize: '0.9rem', opacity: 0.7 }}>
            API: {process.env.REACT_APP_API_URL || 'http://localhost:8000'}
          </div>
        </div>
      </footer>
    </div>
  );
};

export default TVDashboard;
