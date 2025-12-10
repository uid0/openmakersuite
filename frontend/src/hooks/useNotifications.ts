/**
 * useNotifications Hook
 * Convenience hook for accessing notification functionality
 */
import { useNotificationContext } from '../contexts/NotificationContext';

export const useNotifications = () => {
  return useNotificationContext();
};
