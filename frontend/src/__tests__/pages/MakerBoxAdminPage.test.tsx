/**
 * Tests for MakerBoxAdminPage's handling of pre-conversion rows.
 *
 * /maker-boxes/ lists every MakerBox, including rows the pre-conversion
 * workflow has queued with status 'pre_conversion' and no bin_id yet. The
 * admin table must render those rows (it used to throw on the missing status
 * badge and take the whole page down), and the Avery sheet must only be
 * asked for bins that actually have a bin_id.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import MakerBoxAdminPage from '../../pages/MakerBoxAdminPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const makerBox = (overrides: Partial<api.MakerBox> = {}): api.MakerBox => ({
  id: 1,
  bin_id: 'MBX-001',
  assigned_username: 'alice',
  first_name: 'Alice',
  last_name: 'Anderson',
  email: '',
  display_name: 'Alice Anderson',
  assigned_at: null,
  expires_at: null,
  last_verified_at: null,
  status: 'valid',
  identity_source: 'whmcs',
  conversion_completed_at: null,
  paid_at: null,
  notes: '',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
  ...overrides,
});

const queuedBox = makerBox({
  id: 2,
  bin_id: null,
  assigned_username: 'bob',
  first_name: 'Bob',
  last_name: 'Baker',
  display_name: 'Bob Baker',
  status: 'pre_conversion',
});

describe('MakerBoxAdminPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.makerBoxesAPI.labelUrl as jest.Mock).mockImplementation(
      (id: number) => `/api/maker-boxes/${id}/label/`,
    );
  });

  test('renders a pre-conversion row with its own status badge instead of crashing', async () => {
    (api.makerBoxesAPI.list as jest.Mock).mockResolvedValue({
      data: [makerBox(), queuedBox],
    });

    render(<MakerBoxAdminPage />);

    const row = (await screen.findByText('Bob Baker')).closest('tr') as HTMLElement;
    expect(row).toHaveTextContent('Pre-conversion');
    expect(screen.getByText('Alice Anderson').closest('tr')).toHaveTextContent('Valid');
  });

  test('prints the Avery sheet for converted bins only, never a null bin id', async () => {
    (api.makerBoxesAPI.list as jest.Mock).mockResolvedValue({
      data: [makerBox(), queuedBox],
    });
    (api.makerBoxesAPI.printSheet as jest.Mock).mockResolvedValue({ data: new ArrayBuffer(0) });
    window.open = vi.fn();
    URL.createObjectURL = vi.fn(() => 'blob:sheet');

    render(<MakerBoxAdminPage />);

    const button = await screen.findByTestId('print-avery-sheet');
    // The count on the button is the number of cards that will print.
    expect(button).toHaveTextContent('Print Avery sheet (1/10)');
    fireEvent.click(button);

    await waitFor(() => expect(api.makerBoxesAPI.printSheet).toHaveBeenCalledTimes(1));
    expect(api.makerBoxesAPI.printSheet).toHaveBeenCalledWith(['MBX-001']);
  });

  test('disables the Avery sheet when every row is still pre-conversion', async () => {
    (api.makerBoxesAPI.list as jest.Mock).mockResolvedValue({ data: [queuedBox] });

    render(<MakerBoxAdminPage />);

    const button = await screen.findByTestId('print-avery-sheet');
    await screen.findByText('Bob Baker');
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent('Print Avery sheet (0/10)');
  });
});
