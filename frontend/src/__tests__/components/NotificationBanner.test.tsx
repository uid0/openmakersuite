/**
 * NotificationBanner Component Tests
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import NotificationBanner from '../../components/NotificationBanner';
import { BannerNotification } from '../../contexts/NotificationContext';

const renderBanner = (banner: BannerNotification, onDismiss: (id: string) => void) => {
  return render(
    <MantineProvider>
      <NotificationBanner banner={banner} onDismiss={onDismiss} />
    </MantineProvider>
  );
};

describe('NotificationBanner Component', () => {
  const mockDismiss = jest.fn();

  beforeEach(() => {
    mockDismiss.mockClear();
  });

  it('renders success banner', () => {
    const banner: BannerNotification = {
      id: '1',
      type: 'success',
      title: 'Success!',
      message: 'Operation completed successfully',
    };
    renderBanner(banner, mockDismiss);

    expect(screen.getByText('Success!')).toBeInTheDocument();
    expect(screen.getByText('Operation completed successfully')).toBeInTheDocument();
    expect(screen.getByText('✓')).toBeInTheDocument();
  });

  it('renders error banner', () => {
    const banner: BannerNotification = {
      id: '2',
      type: 'error',
      title: 'Error',
      message: 'Something went wrong',
    };
    renderBanner(banner, mockDismiss);

    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('✕')).toBeInTheDocument();
  });

  it('renders warning banner', () => {
    const banner: BannerNotification = {
      id: '3',
      type: 'warning',
      title: 'Warning',
      message: 'Please be careful',
    };
    renderBanner(banner, mockDismiss);

    expect(screen.getByText('Warning')).toBeInTheDocument();
    expect(screen.getByText('Please be careful')).toBeInTheDocument();
    expect(screen.getByText('⚠')).toBeInTheDocument();
  });

  it('renders info banner', () => {
    const banner: BannerNotification = {
      id: '4',
      type: 'info',
      title: 'Information',
      message: 'Here is some info',
    };
    renderBanner(banner, mockDismiss);

    expect(screen.getByText('Information')).toBeInTheDocument();
    expect(screen.getByText('Here is some info')).toBeInTheDocument();
    expect(screen.getByText('ℹ')).toBeInTheDocument();
  });

  it('shows dismiss button for non-persistent banners', () => {
    const banner: BannerNotification = {
      id: '5',
      type: 'info',
      title: 'Test',
      message: 'Test message',
      persistent: false,
    };
    renderBanner(banner, mockDismiss);

    const dismissButton = screen.getByLabelText('Dismiss notification');
    expect(dismissButton).toBeInTheDocument();

    dismissButton.click();
    expect(mockDismiss).toHaveBeenCalledWith('5');
  });

  it('does not show dismiss button for persistent banners', () => {
    const banner: BannerNotification = {
      id: '6',
      type: 'info',
      title: 'Test',
      message: 'Test message',
      persistent: true,
    };
    renderBanner(banner, mockDismiss);

    const dismissButton = screen.queryByLabelText('Dismiss notification');
    expect(dismissButton).not.toBeInTheDocument();
  });
});
