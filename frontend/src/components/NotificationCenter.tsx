/**
 * NotificationCenter Component
 * Displays all notifications in a drawer/modal
 */
import { ActionIcon, Badge, Button, Drawer, Group, ScrollArea, Stack, Text } from '@mantine/core';
import { IconBell, IconTrash } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useModalManager } from '../hooks/useModalManager';
import { useNotifications } from '../hooks/useNotifications';
import '../styles/NotificationCenter.css';
import NotificationBanner from './NotificationBanner';

interface NotificationCenterProps {
  isOpen: boolean;
  onClose: () => void;
}

const NotificationCenter: React.FC<NotificationCenterProps> = ({ isOpen, onClose }) => {
  const navigate = useNavigate();
  const {
    banners,
    backendNotifications,
    unreadCount,
    loadNotifications,
    markAsRead,
    markAllAsRead,
    deleteNotification,
    dismissBanner,
    clearBanners,
  } = useNotifications();
  const [filter, setFilter] = useState<'all' | 'unread'>('unread');

  // Register with modal manager (medium priority)
  useModalManager('notification-center', isOpen, onClose, 50);

  useEffect(() => {
    if (isOpen) {
      loadNotifications();
    }
  }, [isOpen, loadNotifications]);

  const handleNotificationClick = async (notification: typeof backendNotifications[0]) => {
    if (!notification.read) {
      await markAsRead(notification.id);
    }
    if (notification.actionUrl) {
      navigate(notification.actionUrl);
      onClose();
    }
  };

  const handleMarkAllRead = async () => {
    await markAllAsRead();
  };

  const handleDelete = async (id: string) => {
    await deleteNotification(id);
  };

  const getNotificationIcon = (type: string) => {
    switch (type) {
      case 'success':
        return '✓';
      case 'error':
        return '✕';
      case 'warning':
        return '⚠';
      case 'info':
        return 'ℹ';
      default:
        return '•';
    }
  };

  const getNotificationColor = (type: string) => {
    switch (type) {
      case 'success':
        return 'green';
      case 'error':
        return 'red';
      case 'warning':
        return 'yellow';
      case 'info':
        return 'blue';
      default:
        return 'gray';
    }
  };

  const filteredNotifications = filter === 'unread'
    ? backendNotifications.filter((n) => !n.read)
    : backendNotifications;

  const allNotifications = filteredNotifications;

  return (
    <Drawer
      opened={isOpen}
      onClose={onClose}
      title={
        <Group justify="space-between" style={{ width: '100%' }}>
          <Group gap="xs">
            <IconBell size={20} />
            <Text fw={600}>Notifications</Text>
            {unreadCount > 0 && (
              <Badge color="red" size="sm">
                {unreadCount}
              </Badge>
            )}
          </Group>
          <Group gap="xs">
            <Button
              variant="subtle"
              size="xs"
              onClick={() => setFilter(filter === 'all' ? 'unread' : 'all')}
            >
              {filter === 'all' ? 'Unread' : 'All'}
            </Button>
            {unreadCount > 0 && (
              <Button variant="subtle" size="xs" onClick={handleMarkAllRead}>
                Mark all read
              </Button>
            )}
          </Group>
        </Group>
      }
      position="right"
      size="md"
      padding="md"
    >
      <Stack gap="md">
        {/* Banners */}
        {banners.length > 0 && (
          <div>
            <Group justify="space-between" mb="xs">
              <Text size="sm" fw={500}>
                Active Alerts
              </Text>
              <Button variant="subtle" size="xs" onClick={clearBanners}>
                Clear all
              </Button>
            </Group>
            <Stack gap="xs">
              {banners.map((banner) => (
                <NotificationBanner
                  key={banner.id}
                  banner={banner}
                  onDismiss={dismissBanner}
                />
              ))}
            </Stack>
          </div>
        )}

        {/* Backend Notifications */}
        <div>
          <Text size="sm" fw={500} mb="xs">
            {filter === 'unread' ? 'Unread Notifications' : 'All Notifications'}
          </Text>
          <ScrollArea h={400}>
            {allNotifications.length === 0 ? (
              <Text c="dimmed" size="sm" ta="center" py="xl">
                No notifications
              </Text>
            ) : (
              <Stack gap="xs">
                {allNotifications.map((notification) => (
                  <div
                    key={notification.id}
                    className={`notification-item ${notification.read ? 'read' : 'unread'}`}
                    onClick={() => handleNotificationClick(notification)}
                  >
                    <Group gap="sm" align="flex-start" wrap="nowrap">
                      <div
                        className="notification-icon"
                        style={{
                          backgroundColor: `var(--mantine-color-${getNotificationColor(notification.type)}-1)`,
                          color: `var(--mantine-color-${getNotificationColor(notification.type)}-7)`,
                        }}
                      >
                        {getNotificationIcon(notification.type)}
                      </div>
                      <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
                        <Group justify="space-between" gap="xs" wrap="nowrap">
                          <Text size="sm" fw={notification.read ? 400 : 600} lineClamp={1}>
                            {notification.title}
                          </Text>
                          <ActionIcon
                            variant="subtle"
                            size="sm"
                            color="red"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDelete(notification.id);
                            }}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Group>
                        <Text size="xs" c="dimmed" lineClamp={2}>
                          {notification.message}
                        </Text>
                        <Group gap="xs" justify="space-between">
                          <Text size="xs" c="dimmed">
                            {new Date(notification.createdAt).toLocaleString()}
                          </Text>
                          {!notification.read && (
                            <Badge size="xs" color="blue" variant="light">
                              New
                            </Badge>
                          )}
                        </Group>
                      </Stack>
                    </Group>
                  </div>
                ))}
              </Stack>
            )}
          </ScrollArea>
        </div>
      </Stack>
    </Drawer>
  );
};

export default NotificationCenter;
