/**
 * Tests for QRScanner permission/error paths.
 *
 * The component now does an explicit getUserMedia preflight before handing
 * off to html5-qrcode, so we exercise the permission DOMException paths and
 * verify they map to clear, structured error messages with a retry button.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import QRScanner, { QRScannerError } from '../../components/QRScanner';

// html5-qrcode loads the browser's WebAssembly decoder on import, which jsdom
// can't satisfy. We don't reach into it in these tests.
jest.mock('html5-qrcode', () => ({
  Html5Qrcode: jest.fn().mockImplementation(() => ({
    start: jest.fn().mockResolvedValue(undefined),
    stop: jest.fn().mockResolvedValue(undefined),
    clear: jest.fn().mockResolvedValue(undefined),
    getRunningTrackCapabilities: jest.fn().mockReturnValue({}),
    applyVideoConstraints: jest.fn().mockResolvedValue(undefined),
  })),
}));

const setMediaDevices = (overrides: Partial<MediaDevices>) => {
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: jest.fn(),
      enumerateDevices: jest.fn().mockResolvedValue([]),
      ...overrides,
    },
  });
};

const setSecureContext = (value: boolean) => {
  Object.defineProperty(window, 'isSecureContext', {
    configurable: true,
    value,
  });
};

const makeDomException = (name: string): DOMException => {
  const err = new Error(`${name} simulated`) as Error & { name: string };
  err.name = name;
  return err as unknown as DOMException;
};

describe('QRScanner — permission handling', () => {
  beforeEach(() => {
    setSecureContext(true);
  });

  it('shows a clear permission-denied message and notifies the parent on NotAllowedError', async () => {
    const onScanError = jest.fn();
    setMediaDevices({
      getUserMedia: jest.fn().mockRejectedValue(makeDomException('NotAllowedError')),
    });

    render(<QRScanner onScanSuccess={jest.fn()} onScanError={onScanError} />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveAttribute('data-error-kind', 'permission-denied');
    expect(alert).toHaveTextContent(/camera access denied/i);
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();

    await waitFor(() => expect(onScanError).toHaveBeenCalled());
    const lastCallArg = onScanError.mock.calls[onScanError.mock.calls.length - 1][0] as QRScannerError;
    expect(lastCallArg.kind).toBe('permission-denied');
  });

  it('classifies NotFoundError as no-camera', async () => {
    setMediaDevices({
      getUserMedia: jest.fn().mockRejectedValue(makeDomException('NotFoundError')),
    });

    render(<QRScanner onScanSuccess={jest.fn()} />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveAttribute('data-error-kind', 'no-camera');
    expect(alert).toHaveTextContent(/no camera detected/i);
  });

  it('reports insecure-context when window.isSecureContext is false', async () => {
    setSecureContext(false);
    setMediaDevices({});

    render(<QRScanner onScanSuccess={jest.fn()} />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveAttribute('data-error-kind', 'insecure-context');
    expect(alert).toHaveTextContent(/https/i);
  });

  it('clicking "Try again" re-requests permission', async () => {
    const getUserMedia = jest
      .fn()
      .mockRejectedValueOnce(makeDomException('NotAllowedError'))
      .mockResolvedValueOnce({
        getTracks: () => [{ stop: jest.fn() }],
      } as unknown as MediaStream);

    setMediaDevices({ getUserMedia });

    render(<QRScanner onScanSuccess={jest.fn()} />);

    await screen.findByRole('alert');
    expect(getUserMedia).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: /try again/i }));

    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
  });

  it('does not show a generic "Failed to start camera" on permission denial', async () => {
    setMediaDevices({
      getUserMedia: jest.fn().mockRejectedValue(makeDomException('NotAllowedError')),
    });

    render(<QRScanner onScanSuccess={jest.fn()} />);

    const alert = await screen.findByRole('alert');
    expect(alert).not.toHaveTextContent(/failed to start camera/i);
  });
});
