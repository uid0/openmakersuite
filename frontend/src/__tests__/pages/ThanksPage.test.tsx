/**
 * Tests for ThanksPage — closes the documented coverage gap for the
 * public reorder confirmation surface (gh-457, AC-15 "clear final state").
 *
 * ThanksPage is the canonical final-state surface for the anonymous
 * reorder QR flow: submitted scans should land here rather than on a
 * half-submitted form. These tests pin the confirmation message,
 * manual "back to home" affordance, and the auto-redirect timer so
 * regressions to either path are caught.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import ThanksPage from '../../pages/ThanksPage';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <ThanksPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ThanksPage (AC-15: final-state surface for public reorder)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders confirmation eyebrow, title, and description', () => {
    renderPage();

    expect(screen.getByTestId('thanks-page')).toBeInTheDocument();
    expect(screen.getByText(/reorder request received/i)).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: /thanks for letting us know/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/your reorder request has been submitted/i),
    ).toBeInTheDocument();
  });

  test('exposes a manual "Back to home" button that navigates to /', () => {
    renderPage();

    const button = screen.getByRole('button', { name: /back to home/i });
    fireEvent.click(button);

    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  test('auto-redirects to / after the 5-second timer fires', () => {
    jest.useFakeTimers();
    try {
      renderPage();

      expect(mockNavigate).not.toHaveBeenCalled();

      act(() => {
        jest.advanceTimersByTime(4999);
      });
      expect(mockNavigate).not.toHaveBeenCalled();

      act(() => {
        jest.advanceTimersByTime(1);
      });
      expect(mockNavigate).toHaveBeenCalledWith('/');
    } finally {
      jest.useRealTimers();
    }
  });

  test('clears the auto-redirect timer on unmount so unmounted users do not navigate', () => {
    jest.useFakeTimers();
    try {
      const { unmount } = renderPage();
      unmount();

      act(() => {
        jest.advanceTimersByTime(10000);
      });

      expect(mockNavigate).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });
});
