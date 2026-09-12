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
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

// `state` is how ScanPage says WHICH success this was — a request filed, or a
// pending one named. Routed through MemoryRouter's initial entry rather than a
// mocked `useLocation` so the page reads it the way the router really delivers
// it.
const renderPage = (state?: { alreadyRequested?: boolean }) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[{ pathname: '/thanks', state }]}>
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

  // --- Filed, and already recorded are DIFFERENT things to be told ---------
  // The server files at most one PENDING anonymous request per item, so a scan
  // either filed one or named one that was already on file. Both are successes
  // and both end here; what neither may read as is a failure, and what
  // "already recorded" may not claim is that a second request was filed.

  test('an already-recorded scan is told its need is on file, not that it filed one', () => {
    renderPage({ alreadyRequested: true });

    expect(
      screen.getByRole('heading', { name: /already recorded/i }),
    ).toBeInTheDocument();
    const body = screen.getByTestId('thanks-body');
    expect(body).toHaveTextContent(/already open/i);
    expect(body).toHaveTextContent(/your need is recorded/i);
    // Never a failure, and never an instruction to act again.
    expect(body).not.toHaveTextContent(/could not|failed|try again|ask a member of staff/i);
    // And it does not claim a request was just submitted.
    expect(screen.queryByText(/your reorder request has been submitted/i)).toBeNull();
  });

  test('a filed scan is not worded as a duplicate', () => {
    renderPage({ alreadyRequested: false });

    expect(
      screen.getByRole('heading', { name: /thanks for letting us know/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('thanks-body')).toHaveTextContent(
      /your reorder request has been submitted/i,
    );
  });

  test('a direct visit with no state gets the plain wording', () => {
    // A reload or a back-navigation loses the state. The plain wording claims
    // nothing about which outcome it was beyond "your request is in", which is
    // true of both — better than guessing the duplicate wording for someone who
    // just filed.
    renderPage();

    expect(
      screen.getByRole('heading', { name: /thanks for letting us know/i }),
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
