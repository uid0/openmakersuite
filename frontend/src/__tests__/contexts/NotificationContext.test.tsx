/**
 * NotificationContext Tests
 */
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { act, renderHook } from '@testing-library/react';
import React from 'react';
import { NotificationProvider, useNotificationContext } from '../../contexts/NotificationContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MantineProvider>
    <NotificationProvider>
      <Notifications />
      {children}
    </NotificationProvider>
  </MantineProvider>
);

describe('NotificationContext', () => {
  it('provides notification functions', () => {
    const { result } = renderHook(() => useNotificationContext(), { wrapper });

    expect(result.current.showSuccess).toBeDefined();
    expect(result.current.showError).toBeDefined();
    expect(result.current.showWarning).toBeDefined();
    expect(result.current.showInfo).toBeDefined();
    expect(result.current.showBanner).toBeDefined();
    expect(result.current.dismissBanner).toBeDefined();
  });

  it('manages banner state', () => {
    const { result } = renderHook(() => useNotificationContext(), { wrapper });

    expect(result.current.banners).toEqual([]);

    act(() => {
      const id = result.current.showBanner('info', 'Test Title', 'Test Message');
      expect(id).toBeDefined();
    });

    expect(result.current.banners.length).toBe(1);
    expect(result.current.banners[0].title).toBe('Test Title');
    expect(result.current.banners[0].message).toBe('Test Message');
    expect(result.current.banners[0].type).toBe('info');

    act(() => {
      result.current.dismissBanner(result.current.banners[0].id);
    });

    expect(result.current.banners.length).toBe(0);
  });

  it('manages backend notifications state', () => {
    const { result } = renderHook(() => useNotificationContext(), { wrapper });

    expect(result.current.backendNotifications).toEqual([]);
    expect(result.current.unreadCount).toBe(0);
  });
});
