/**
 * NotificationBanner Component
 * Displays persistent banner notifications for complex actions and errors
 */
import React from 'react';
import { BannerNotification } from '../contexts/NotificationContext';
import '../styles/NotificationBanner.css';

interface NotificationBannerProps {
  banner: BannerNotification;
  onDismiss: (id: string) => void;
}

const NotificationBanner: React.FC<NotificationBannerProps> = ({ banner, onDismiss }) => {
  const getIcon = () => {
    switch (banner.type) {
      case 'success':
        return '✓';
      case 'error':
        return '✕';
      case 'warning':
        return '⚠';
      case 'info':
        return 'ℹ';
      default:
        return '';
    }
  };

  const getClassName = () => {
    return `notification-banner notification-banner-${banner.type}`;
  };

  return (
    <div className={getClassName()}>
      <div className="notification-banner-content">
        <span className="notification-banner-icon">{getIcon()}</span>
        <div className="notification-banner-text">
          <strong className="notification-banner-title">{banner.title}</strong>
          <span className="notification-banner-message">{banner.message}</span>
        </div>
      </div>
      {!banner.persistent && (
        <button
          className="notification-banner-dismiss"
          onClick={() => onDismiss(banner.id)}
          aria-label="Dismiss notification"
        >
          ×
        </button>
      )}
    </div>
  );
};

export default NotificationBanner;
