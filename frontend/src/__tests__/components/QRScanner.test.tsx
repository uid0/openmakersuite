/**
 * Tests for QRScanner permission/error paths.
 *
 * The component now does an explicit getUserMedia preflight before handing
 * off to html5-qrcode, so we exercise the permission DOMException paths and
 * verify they map to clear, structured error messages with a retry button.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Html5Qrcode } from 'html5-qrcode';
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

// Babel-jest in CRA's preset can't parse `jest.Mock` in type-cast positions,
// so we bounce through `unknown` and rely on duck typing for `mockClear` /
// `mockImplementation` / `mock.results` access in tests.
const Html5QrcodeMock = Html5Qrcode as unknown as {
  mockClear: () => void;
  mockImplementation: (fn: () => unknown) => void;
  mock: { results: { value: unknown }[] };
};

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

describe('QRScanner — camera lifecycle', () => {
  beforeEach(() => {
    setSecureContext(true);
    Html5QrcodeMock.mockClear();
  });

  it('releases the pre-flight MediaStream tracks before invoking Html5Qrcode.start', async () => {
    const trackStop = jest.fn();
    const stream = {
      getTracks: () => [{ stop: trackStop }],
    } as unknown as MediaStream;

    let trackStoppedAtStart = false;
    const start = jest.fn().mockImplementation(async () => {
      trackStoppedAtStart = trackStop.mock.calls.length > 0;
    });

    Html5QrcodeMock.mockImplementation(() => ({
      start,
      stop: jest.fn().mockResolvedValue(undefined),
      clear: jest.fn().mockResolvedValue(undefined),
      getRunningTrackCapabilities: jest.fn().mockReturnValue({}),
      applyVideoConstraints: jest.fn().mockResolvedValue(undefined),
    }));

    setMediaDevices({
      getUserMedia: jest.fn().mockResolvedValue(stream),
      enumerateDevices: jest.fn().mockResolvedValue([
        { kind: 'videoinput', deviceId: 'cam-1', label: 'Back', groupId: '' } as MediaDeviceInfo,
      ]),
    });

    render(<QRScanner onScanSuccess={jest.fn()} />);

    await waitFor(() => expect(start).toHaveBeenCalled());

    expect(trackStop).toHaveBeenCalledTimes(1);
    expect(trackStoppedAtStart).toBe(true);
  });

  it('stops the library and clears the camera when the component unmounts mid-scan', async () => {
    // Regression: previously stopScanning() gated on the `isScanning` state,
    // but the effect cleanup captured a stale closure (isScanning still false
    // because setIsScanning(true) had not yet propagated). The stop() call was
    // skipped, leaving the camera/MediaStream alive — next scanner open then
    // failed with NotReadableError ("camera in use").
    const stop = jest.fn().mockResolvedValue(undefined);
    const clear = jest.fn().mockResolvedValue(undefined);
    Html5QrcodeMock.mockImplementation(() => ({
      start: jest.fn().mockResolvedValue(undefined),
      stop,
      clear,
      getRunningTrackCapabilities: jest.fn().mockReturnValue({}),
      applyVideoConstraints: jest.fn().mockResolvedValue(undefined),
    }));

    setMediaDevices({
      getUserMedia: jest.fn().mockResolvedValue({
        getTracks: () => [{ stop: jest.fn() }],
      } as unknown as MediaStream),
      enumerateDevices: jest.fn().mockResolvedValue([
        { kind: 'videoinput', deviceId: 'cam-1', label: 'Back', groupId: '' } as MediaDeviceInfo,
      ]),
    });

    const { unmount } = render(<QRScanner onScanSuccess={jest.fn()} />);

    // Wait for the scanner to actually start before unmounting.
    await waitFor(() =>
      expect((Html5QrcodeMock.mock.results[0]?.value as { start: jest.Mock }).start).toHaveBeenCalled()
    );

    unmount();

    await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it('only fires onScanSuccess once even if the library invokes the callback multiple times', async () => {
    let successCallback: ((text: string) => void) | undefined;
    const start = jest.fn().mockImplementation(async (_camera, _config, onSuccess) => {
      successCallback = onSuccess;
    });
    Html5QrcodeMock.mockImplementation(() => ({
      start,
      stop: jest.fn().mockResolvedValue(undefined),
      clear: jest.fn().mockResolvedValue(undefined),
      getRunningTrackCapabilities: jest.fn().mockReturnValue({}),
      applyVideoConstraints: jest.fn().mockResolvedValue(undefined),
    }));

    setMediaDevices({
      getUserMedia: jest.fn().mockResolvedValue({
        getTracks: () => [{ stop: jest.fn() }],
      } as unknown as MediaStream),
      enumerateDevices: jest.fn().mockResolvedValue([
        { kind: 'videoinput', deviceId: 'cam-1', label: 'Back', groupId: '' } as MediaDeviceInfo,
      ]),
    });

    const onScanSuccess = jest.fn();
    render(<QRScanner onScanSuccess={onScanSuccess} />);

    await waitFor(() => expect(successCallback).toBeDefined());

    await act(async () => {
      successCallback!('CODE-123');
      successCallback!('CODE-123');
      successCallback!('CODE-456');
    });

    expect(onScanSuccess).toHaveBeenCalledTimes(1);
    expect(onScanSuccess).toHaveBeenCalledWith('CODE-123');
  });
});
