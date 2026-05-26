/**
 * Notification Context
 * Provides notification functionality throughout the application
 */
import { notifications } from '@mantine/notifications';
import React, { createContext, ReactNode, useCallback, useContext, useState } from 'react';

import { notificationsAPI } from '../services/api';

export type NotificationType = 'success' | 'error' | 'warning' | 'info';

export interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  message: string;
  read: boolean;
  createdAt: Date;
  actionUrl?: string;
  metadata?: Record<string, unknown>;
}

export interface BannerNotification {
  id: string;
  type: NotificationType;
  title: string;
  message: string;
  persistent?: boolean;
  onDismiss?: () => void;
}

interface NotificationContextType {
  // Toast notifications (auto-dismiss)
  showToast: (type: NotificationType, title: string, message?: string, options?: { autoClose?: number }) => void;
  showSuccess: (title: string, message?: string) => void;
  showError: (title: string, message?: string) => void;
  showWarning: (title: string, message?: string) => void;
  showInfo: (title: string, message?: string) => void;

  // Banner notifications (persistent)
  banners: BannerNotification[];
  showBanner: (type: NotificationType, title: string, message: string, persistent?: boolean) => string;
  dismissBanner: (id: string) => void;
  clearBanners: () => void;

  // Backend notifications
  backendNotifications: Notification[];
  unreadCount: number;
  loadNotifications: () => Promise<void>;
  markAsRead: (id: string) => Promise<void>;
  markAllAsRead: () => Promise<void>;
  deleteNotification: (id: string) => Promise<void>;
}

const NotificationContext = createContext<NotificationContextType | undefined>(undefined);

export const useNotificationContext = () => {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error('useNotificationContext must be used within NotificationProvider');
  }
  return context;
};

interface NotificationProviderProps {
  children: ReactNode;
}

export const NotificationProvider: React.FC<NotificationProviderProps> = ({ children }) => {
  const [banners, setBanners] = useState<BannerNotification[]>([]);
  const [backendNotifications, setBackendNotifications] = useState<Notification[]>([]);

  // Toast notifications
  const showToast = useCallback((
    type: NotificationType,
    title: string,
    message?: string,
    options?: { autoClose?: number }
  ) => {
    const colorMap = {
      success: 'green',
      error: 'red',
      warning: 'yellow',
      info: 'blue',
    };

    notifications.show({
      title,
      message: message || '',
      color: colorMap[type],
      autoClose: options?.autoClose ?? (type === 'error' ? 5000 : 3000),
    });
  }, []);

  const showSuccess = useCallback((title: string, message?: string) => {
    showToast('success', title, message);
  }, [showToast]);

  const showError = useCallback((title: string, message?: string) => {
    showToast('error', title, message, { autoClose: 5000 });
  }, [showToast]);

  const showWarning = useCallback((title: string, message?: string) => {
    showToast('warning', title, message);
  }, [showToast]);

  const showInfo = useCallback((title: string, message?: string) => {
    showToast('info', title, message);
  }, [showToast]);

  // Banner notifications
  const showBanner = useCallback((
    type: NotificationType,
    title: string,
    message: string,
    persistent: boolean = true
  ): string => {
    const id = `banner-${Date.now()}-${Math.random()}`;
    const banner: BannerNotification = {
      id,
      type,
      title,
      message,
      persistent,
    };
    setBanners((prev) => [...prev, banner]);
    return id;
  }, []);

  const dismissBanner = useCallback((id: string) => {
    setBanners((prev) => prev.filter((b) => b.id !== id));
  }, []);

  const clearBanners = useCallback(() => {
    setBanners([]);
  }, []);

  // Backend notifications
  const loadNotifications = useCallback(async () => {
    try {
const response = await notificationsAPI.list();
      const notifications: Notification[] = response.data.results.map((n) => ({
        id: n.id,
        type: n.type,
        title: n.title,
        message: n.message,
        read: n.read,
        createdAt: new Date(n.created_at),
        actionUrl: n.action_url,
        metadata: n.metadata,
      }));
      setBackendNotifications(notifications);
    } catch (error) {
      console.error('Failed to load notifications:', error);
    }
  }, []);

  const markAsRead = useCallback(async (id: string) => {
    try {
await notificationsAPI.markAsRead(id);
      setBackendNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, read: true } : n))
      );
    } catch (error) {
      console.error('Failed to mark notification as read:', error);
    }
  }, []);

  const markAllAsRead = useCallback(async () => {
    try {
await notificationsAPI.markAllAsRead();
      setBackendNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    } catch (error) {
      console.error('Failed to mark all notifications as read:', error);
    }
  }, []);

  const deleteNotification = useCallback(async (id: string) => {
    try {
await notificationsAPI.delete(id);
      setBackendNotifications((prev) => prev.filter((n) => n.id !== id));
    } catch (error) {
      console.error('Failed to delete notification:', error);
    }
  }, []);

  const unreadCount = backendNotifications.filter((n) => !n.read).length;

  const value: NotificationContextType = {
    showToast,
    showSuccess,
    showError,
    showWarning,
    showInfo,
    banners,
    showBanner,
    dismissBanner,
    clearBanners,
    backendNotifications,
    unreadCount,
    loadNotifications,
    markAsRead,
    markAllAsRead,
    deleteNotification,
  };

  return (
    <NotificationContext.Provider value={value}>
      {children}
    </NotificationContext.Provider>
  );
};
