/**
 * NotificationBadge Component
 * Displays unread notification count badge
 */
import React from 'react';
import '../styles/NotificationBadge.css';

interface NotificationBadgeProps {
  count: number;
  onClick?: () => void;
}

const NotificationBadge: React.FC<NotificationBadgeProps> = ({ count, onClick }) => {
  if (count === 0) {
    return null;
  }

  return (
    <button
      className="notification-badge"
      onClick={onClick}
      aria-label={`${count} unread notifications`}
    >
      <span className="notification-badge-count">{count > 99 ? '99+' : count}</span>
    </button>
  );
};

export default NotificationBadge;
