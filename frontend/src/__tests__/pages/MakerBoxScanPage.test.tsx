/**
 * Tests for MakerBoxScanPage — closes the documented coverage gap for the
 * staff-driven Maker Box scan-and-status workflow (gh-457, AC-14 code-entry
 * fallback / AC-15 duplicate-submit guard / AC-19 actionable error state).
 *
 * Maker Box scan is camera-free by design — the inputs are the code-entry
 * fallback themselves — so these tests pin the submit lifecycle, the
 * status-color rendering for valid / grace / expired / unknown, the
 * pickup-email affordance on expired/unknown, and the error surface that
 * shows when the API rejects the scan.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import MakerBoxScanPage from '../../pages/MakerBoxScanPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

type ScanResult = api.MakerBoxScanResult;

const scanResult = (overrides: Partial<ScanResult> = {}): ScanResult => ({
  bin_id: 'PSB-007',
  username: 'alice',
  first_name: 'Alice',
  last_name: 'Anderson',
  status: 'valid',
  days_remaining: 30,
  ...overrides,
});

const fillAndSubmit = (binId = 'PSB-007', username = 'alice') => {
  fireEvent.change(screen.getByLabelText(/bin id/i), { target: { value: binId } });
  fireEvent.change(screen.getByLabelText(/username/i), { target: { value: username } });
  fireEvent.click(screen.getByRole('button', { name: /^scan$/i }));
};

describe('MakerBoxScanPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (api.makerBoxesAPI.list as jest.Mock).mockResolvedValue({ data: [] });
  });

  test('renders the camera-free code-entry form with both inputs', () => {
    render(<MakerBoxScanPage />);

    expect(screen.getByRole('heading', { name: /maker box scan/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/bin id/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^scan$/i })).toBeInTheDocument();
  });

  test('renders VALID MEMBER status with green styling and days remaining', async () => {
    (api.makerBoxesAPI.scan as jest.Mock).mockResolvedValue({
      data: scanResult({ status: 'valid', days_remaining: 30 }),
    });

    render(<MakerBoxScanPage />);
    fillAndSubmit();

    const result = await screen.findByTestId('scan-result');
    expect(result).toHaveAttribute('data-status', 'valid');
    expect(result).toHaveTextContent(/valid member/i);
    expect(result).toHaveTextContent(/30 days remaining/i);
    expect(result).toHaveTextContent(/alice anderson/i);
  });

  test('renders GRACE PERIOD status when membership is in grace window', async () => {
    (api.makerBoxesAPI.scan as jest.Mock).mockResolvedValue({
      data: scanResult({ status: 'grace', days_remaining: 4 }),
    });

    render(<MakerBoxScanPage />);
    fillAndSubmit();

    const result = await screen.findByTestId('scan-result');
    expect(result).toHaveAttribute('data-status', 'grace');
    expect(result).toHaveTextContent(/grace period/i);
    expect(result).toHaveTextContent(/4 days of grace remaining/i);
  });

  test('shows pickup-email button for expired members and calls API on click', async () => {
    (api.makerBoxesAPI.scan as jest.Mock).mockResolvedValue({
      data: scanResult({ status: 'expired', days_remaining: 0 }),
    });
    (api.makerBoxesAPI.list as jest.Mock).mockResolvedValue({
      data: { results: [{ id: 42, bin_id: 'PSB-007' }] },
    });
    (api.makerBoxesAPI.emailPickup as jest.Mock).mockResolvedValue({
      data: { sent: true, to: 'alice@example.com' },
    });

    render(<MakerBoxScanPage />);
    fillAndSubmit();

    const button = await screen.findByRole('button', { name: /send pickup email/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(api.makerBoxesAPI.emailPickup).toHaveBeenCalledWith(42);
    });
    await screen.findByRole('button', { name: /email sent to alice@example.com/i });
  });

  test('shows UNKNOWN USER status when WHMCS has no record', async () => {
    (api.makerBoxesAPI.scan as jest.Mock).mockResolvedValue({
      data: scanResult({ status: 'unknown', first_name: '', last_name: '' }),
    });

    render(<MakerBoxScanPage />);
    fillAndSubmit();

    const result = await screen.findByTestId('scan-result');
    expect(result).toHaveAttribute('data-status', 'unknown');
    expect(result).toHaveTextContent(/unknown user/i);
    expect(result).toHaveTextContent(/no record of this username/i);
  });

  test('shows an actionable error in role=alert when the scan request fails', async () => {
    (api.makerBoxesAPI.scan as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Bin not found' } },
    });

    render(<MakerBoxScanPage />);
    fillAndSubmit('NOPE', 'ghost');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/bin not found/i);
    expect(screen.queryByTestId('scan-result')).not.toBeInTheDocument();
  });

  test('Reset clears form, result, and error so the next scan starts fresh', async () => {
    (api.makerBoxesAPI.scan as jest.Mock).mockResolvedValue({
      data: scanResult({ status: 'valid', days_remaining: 30 }),
    });

    render(<MakerBoxScanPage />);
    fillAndSubmit();

    await screen.findByTestId('scan-result');
    fireEvent.click(screen.getByRole('button', { name: /^reset$/i }));

    expect(screen.queryByTestId('scan-result')).not.toBeInTheDocument();
    expect(screen.getByLabelText(/bin id/i)).toHaveValue('');
    expect(screen.getByLabelText(/username/i)).toHaveValue('');
  });

  // R2 resilience (oms-sr1l4): AC-16 — when the scan request fails with a bare
  // network error (offline, no response body), the page still shows the
  // fallback "Scan failed." alert rather than crashing or going blank.
  test('AC-16: a scan made offline shows the fallback "Scan failed." alert', async () => {
    (api.makerBoxesAPI.scan as jest.Mock).mockRejectedValue(networkError());

    render(<MakerBoxScanPage />);
    fillAndSubmit();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/scan failed/i);
    expect(screen.queryByTestId('scan-result')).not.toBeInTheDocument();
  });
});
