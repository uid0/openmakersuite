/**
 * TV Dashboard Component - Optimized for Chromecast/TV display
 * Shows items that have been reordered and are in progress
 * Features: Location-specific filtering, QR codes, anti-burn-in
 */
import axios from 'axios';
import { QRCodeSVG as QRCode } from 'qrcode.react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useSiteSettings } from '../hooks/useSiteSettings';
import { resolveApiBaseUrl } from '../services/api';
import '../styles/InventoryList.css';
import '../styles/TVDashboard.css';
import { InventoryItem } from '../types';

// Create a dedicated API instance for TV Dashboard that doesn't send auth headers
const tvAPI = axios.create({
  baseURL: resolveApiBaseUrl(),
  headers: {
    'Content-Type': 'application/json',
  },
});

const TVDashboard: React.FC = () => {
  const { location } = useParams<{ location?: string }>();
  const { settings: siteSettings } = useSiteSettings();
  const [reorderedItems, setReorderedItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [currentMessageIndex, setCurrentMessageIndex] = useState(0);
  const [burnInOffset, setBurnInOffset] = useState({ x: 0, y: 0 });
  const [itemOrder, setItemOrder] = useState<number[]>([]);
  const [emptyStateSlide, setEmptyStateSlide] = useState(0); // 0 = "No items", 1 = "How to scan"
  const [currentItemRotationIndex, setCurrentItemRotationIndex] = useState(0); // For rotating through items when > 3

  // Location-specific configuration with site settings support
  // Memoize config to prevent unnecessary re-renders and API calls
  const config = useMemo(() => {
    // Use site settings if available, fall back to environment variables
    const siteName = siteSettings?.site_name || process.env.REACT_APP_DASHBOARD_TITLE || 'Dallas Makerspace Inventory';
    const dashboardTitle = siteSettings?.dashboard_title || siteName;
    const dashboardSubtitle = siteSettings?.dashboard_subtitle || process.env.REACT_APP_DASHBOARD_SUBTITLE || 'Items on Order';
    const logo = siteSettings?.logo_url || process.env.REACT_APP_DASHBOARD_LOGO || null;
    const showLogo = siteSettings?.show_logo_on_dashboard !== false && process.env.REACT_APP_SHOW_LOGO !== 'false';

    const baseConfig = {
      title: dashboardTitle,
      subtitle: dashboardSubtitle,
      logo: logo,
      showLogo: showLogo,
      showTransparency: process.env.REACT_APP_SHOW_TRANSPARENCY !== 'false',
      primaryColor: siteSettings?.primary_color || '#007cba',
      secondaryColor: siteSettings?.secondary_color || '#417690',
    };

    // Location-specific overrides (environment variables take precedence for location-specific configs)
    if (location) {
      const locationUpper = location.toUpperCase();
      return {
        ...baseConfig,
        title: process.env[`REACT_APP_DASHBOARD_TITLE_${locationUpper}`] || `${baseConfig.title} - ${location.charAt(0).toUpperCase() + location.slice(1)}`,
        subtitle: process.env[`REACT_APP_DASHBOARD_SUBTITLE_${locationUpper}`] || baseConfig.subtitle,
        logo: process.env[`REACT_APP_DASHBOARD_LOGO_${locationUpper}`] || baseConfig.logo,
        locationFilter: location.toLowerCase(),
      };
    }

    return baseConfig;
  }, [siteSettings, location]);

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

  // Extract locationFilter to make fetchReorderedItems more stable
  const locationFilter = useMemo(() => (config as any).locationFilter, [config]);

  const fetchReorderedItems = useCallback(async () => {
    try {
      setError(null);
      // Use dedicated TV API instance that doesn't send auth headers
      const response = await tvAPI.get<InventoryItem[]>('/inventory/items/reordered/');

      // Filter items by location if specified
      let filteredItems = response.data;
      if (locationFilter) {
        filteredItems = response.data.filter(item =>
          item.location && item.location.toLowerCase().includes(locationFilter)
        );
      }

      setReorderedItems(filteredItems);
      // Initialize item order for anti-burn-in
      setItemOrder(filteredItems.map((_, index) => index));
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
  }, [locationFilter]);

  useEffect(() => {
    // Initial fetch
    fetchReorderedItems();

    // Set up auto-refresh every 30 seconds
    const interval = setInterval(fetchReorderedItems, 30000);

    return () => clearInterval(interval);
  }, [fetchReorderedItems]);

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

  // Empty state carousel rotation (when no items on order)
  useEffect(() => {
    if (reorderedItems.length > 0) return; // Only run when empty

    const interval = setInterval(() => {
      setEmptyStateSlide((prev) => (prev + 1) % 2); // Toggle between 0 and 1
    }, 8000); // Switch every 8 seconds

    return () => clearInterval(interval);
  }, [reorderedItems.length]);

  // Anti-burn-in effects
  useEffect(() => {
    // Pixel shifting every 90 seconds to prevent burn-in (more frequent)
    const pixelShiftInterval = setInterval(() => {
      setBurnInOffset(prev => ({
        x: (Math.random() * 6 - 3), // Random shift between -3 and 3 pixels
        y: (Math.random() * 6 - 3)
      }));
    }, 90000); // 90 seconds

    // Content reordering every 2 minutes (more frequent)
    const reorderInterval = setInterval(() => {
      setItemOrder(prevOrder => {
        const newOrder = [...prevOrder];
        // Fisher-Yates shuffle algorithm for random reordering
        for (let i = newOrder.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [newOrder[i], newOrder[j]] = [newOrder[j], newOrder[i]];
        }
        return newOrder;
      });
    }, 120000); // 2 minutes

    return () => {
      clearInterval(pixelShiftInterval);
      clearInterval(reorderInterval);
    };
  }, []);

  // Item rotation - if more than 3 items, rotate through them showing 3 at a time
  useEffect(() => {
    if (reorderedItems.length <= 3) {
      setCurrentItemRotationIndex(0);
      return;
    }

    // Reset rotation index when items change
    setCurrentItemRotationIndex(0);

    // Rotate every 15 seconds to show different items
    const rotationInterval = setInterval(() => {
      setCurrentItemRotationIndex(prev => {
        const maxIndex = Math.ceil(reorderedItems.length / 3) - 1;
        return (prev + 1) % (maxIndex + 1);
      });
    }, 15000); // 15 seconds

    return () => clearInterval(rotationInterval);
  }, [reorderedItems.length]);

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
    const baseUrl = resolveApiBaseUrl();
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
          <div className="loading-spinner" data-testid="loading-spinner"></div>
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

  // Generate transparency QR code URL
  const getTransparencyQRUrl = () => {
    const baseUrl = window.location.origin;
    return `${baseUrl}/transparency`;
  };

  return (
      <div
      className="tv-dashboard"
      style={{
        transform: `translate(${burnInOffset.x}px, ${burnInOffset.y}px)`,
        transition: 'transform 3s ease-in-out',
        animation: 'subtlePulse 30s ease-in-out infinite'
      }}
    >
      <header className="dashboard-header">
        <div className="header-content">
          {config.showLogo && config.logo && (
            <div className="logo-container">
              <img src={config.logo} alt={siteSettings?.logo_alt_text || "Logo"} className="dashboard-logo" />
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
          <div className="empty-state-carousel">
            {emptyStateSlide === 0 ? (
              <div className="no-items slide-in">
                <div className="no-items-icon">✅</div>
                <h2>No Items on Order</h2>
                <p>All reorder requests have been completed</p>
              </div>
            ) : (
              <div className="scan-instructions slide-in">
                <h2 className="instructions-title">
                  <span className="icon-large">📱</span>
                  How to Request a Reorder
                </h2>
                <div className="instruction-content">
                  <div className="sample-card-container">
                    {/* Use actual inventory card styling */}
                    <div className="inventory-card low-stock sample-card-demo">
                      <div className="item-details">
                        <h3 className="item-name">Power Tool Batteries</h3>
                        <p className="item-sku">SKU: BATT-18V-5AH</p>
                        <span className="item-category">Workshop Supplies</span>

                        <div className="item-stock">
                          <div className="stock-row">
                            <span className="stock-label">Current:</span>
                            <span className="stock-value low">3</span>
                          </div>
                          <div className="stock-row">
                            <span className="stock-label">Minimum:</span>
                            <span className="stock-value">10</span>
                          </div>
                        </div>

                        <div className="reorder-badge">
                          ⚠️ Needs Reorder (12 units)
                        </div>

                        <div className="item-location">
                          📍 Main Workshop
                        </div>

                        <div className="sample-qr-highlight">
                          <QRCode
                            value="https://example.com/scan"
                            size={140}
                            bgColor="#ffffff"
                            fgColor="#000000"
                            level="M"
                          />
                          <div className="qr-pulse"></div>
                        </div>
                      </div>
                    </div>
                    <div className="giant-arrow">
                      <svg width="200" height="200" viewBox="0 0 200 200">
                        <defs>
                          <marker
                            id="arrowhead"
                            markerWidth="10"
                            markerHeight="10"
                            refX="9"
                            refY="3"
                            orient="auto"
                          >
                            <polygon points="0 0, 10 3, 0 6" fill="#fbbf24" />
                          </marker>
                        </defs>
                        <path
                          d="M 20 100 Q 100 20 180 100"
                          stroke="#fbbf24"
                          strokeWidth="8"
                          fill="none"
                          markerEnd="url(#arrowhead)"
                          className="arrow-path"
                        />
                      </svg>
                      <span className="arrow-label">SCAN HERE!</span>
                    </div>
                  </div>
                  <div className="instruction-steps">
                    <div className="step">
                      <span className="step-number">1</span>
                      <span>Find the item shelf label</span>
                    </div>
                    <div className="step">
                      <span className="step-number">2</span>
                      <span>Open your phone camera</span>
                    </div>
                    <div className="step">
                      <span className="step-number">3</span>
                      <span>Scan the QR code</span>
                    </div>
                    <div className="step">
                      <span className="step-number">4</span>
                      <span>Submit reorder request</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="items-grid">
            {(() => {
              // If more than 3 items, show only 3 at a time with rotation
              const itemsToShow = reorderedItems.length > 3
                ? itemOrder.slice(
                    currentItemRotationIndex * 3,
                    (currentItemRotationIndex * 3) + 3
                  )
                : itemOrder;

              return itemsToShow.map((originalIndex) => {
                if (originalIndex >= reorderedItems.length) return null;
                const item = reorderedItems[originalIndex];
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
                              '--progress-width': `${progressPercentage}%`,
                              '--progress-width-num': progressPercentage
                            } as React.CSSProperties & { '--progress-width': string; '--progress-width-num': number }}
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
              });
            })()}
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
            <div className="transparency-qr">
              <div className="qr-section">
                <QRCode
                  value={getTransparencyQRUrl()}
                  size={80}
                  bgColor="#ffffff"
                  fgColor="#000000"
                  level="M"
                />
                <div className="qr-label">
                  <span className="transparency-icon">🔍</span>
                  <span>Financial Transparency</span>
                  <small>Scan to view</small>
                </div>
              </div>
            </div>
          )}
          <div className="debug-info" style={{ fontSize: '0.9rem', opacity: 0.7 }}>
            {location ? `Location: ${location} | ` : ''}API: {resolveApiBaseUrl()}
          </div>
        </div>
      </footer>
    </div>
  );
};

export default TVDashboard;
