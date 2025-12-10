/**
 * useNotifications Hook Tests
 */
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { renderHook } from '@testing-library/react';
import React from 'react';
import { NotificationProvider } from '../../contexts/NotificationContext';
import { useNotifications } from '../../hooks/useNotifications';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MantineProvider>
    <NotificationProvider>
      <Notifications />
      {children}
    </NotificationProvider>
  </MantineProvider>
);

describe('useNotifications Hook', () => {
  it('returns notification context', () => {
    const { result } = renderHook(() => useNotifications(), { wrapper });

    expect(result.current.showSuccess).toBeDefined();
    expect(result.current.showError).toBeDefined();
    expect(result.current.showWarning).toBeDefined();
    expect(result.current.showInfo).toBeDefined();
    expect(result.current.banners).toBeDefined();
    expect(result.current.backendNotifications).toBeDefined();
    expect(result.current.unreadCount).toBeDefined();
  });
});
