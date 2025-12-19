/**
 * Offline Indicator Component
 * Shows a visual indicator when the app is offline
 */
import { Alert } from '@mantine/core';
import { IconWifiOff } from '@tabler/icons-react';
import React from 'react';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import '../styles/OfflineIndicator.css';

const OfflineIndicator: React.FC = () => {
  const { isOnline } = useOnlineStatus();

  if (isOnline) {
    return null;
  }

  return (
    <Alert
      className="offline-indicator"
      icon={<IconWifiOff size={16} />}
      title="You're offline"
      color="orange"
      variant="light"
      radius="md"
    >
      Some features may be limited. Your data will sync when you're back online.
    </Alert>
  );
};

export default OfflineIndicator;
