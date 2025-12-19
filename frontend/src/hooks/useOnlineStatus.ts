/**
 * Custom hook for monitoring online/offline status
 * Uses navigator.onLine and network events to track connection status
 */

import { useEffect, useState } from 'react';

export interface UseOnlineStatusReturn {
  isOnline: boolean;
  wasOffline: boolean;
}

export function useOnlineStatus(): UseOnlineStatusReturn {
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [wasOffline, setWasOffline] = useState(false);

  useEffect(() => {
    const handleOnline = () => {
      setIsOnline(true);
      // Track that we were offline at some point
      if (wasOffline) {
        // Optionally reload the page to sync data
        // window.location.reload();
      }
    };

    const handleOffline = () => {
      setIsOnline(false);
      setWasOffline(true);
    };

    // Listen to online/offline events
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    // Also listen to visibility change to check status when tab becomes visible
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        setIsOnline(navigator.onLine);
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [wasOffline]);

  return {
    isOnline,
    wasOffline,
  };
}
