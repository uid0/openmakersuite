/**
 * Universal Scanner (`/scan`) — how a project-storage stint scan is LABELLED.
 *
 * The dispatcher resolves a PS-XXX label QR to action 'project_storage_stint'.
 * Every other action has an operator-facing label and badge colour; a stint
 * scan must too, or the success toast reads "undefined: PS-042" and the scan
 * history badge is empty.
 */
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications, cleanNotifications } from '@mantine/notifications';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import UniversalScannerPage from '../../pages/UniversalScannerPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => jest.fn(),
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <ModalsProvider>
        <Notifications />
        <MemoryRouter>
          <UniversalScannerPage />
        </MemoryRouter>
      </ModalsProvider>
    </MantineProvider>,
  );

const scan = async (payload: string) => {
  const input = await screen.findByTestId('universal-scanner-input');
  fireEvent.change(input, { target: { value: payload } });
  fireEvent.keyDown(input, { key: 'Enter' });
};

describe('UniversalScannerPage — project storage stint scan label', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // The Mantine notification store is module-global; left dirty it leaks
    // between cases in the same file.
    cleanNotifications();
    (api.scannerAPI.dispatch as jest.Mock).mockResolvedValue({
      data: {
        action: 'project_storage_stint',
        target_type: 'project_storage_stint',
        target_id: 'stint-42',
        target_name: 'PS-042',
        target_url: '/facilities/project-storage/stint-42',
        raw_payload: 'PS-042',
      },
    });
  });

  test('the success toast names the action, never "undefined"', async () => {
    renderPage();
    await scan('PS-042');

    await screen.findByText('Project storage: PS-042');
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
  });

  test('the history badge shows the label in its own colour', async () => {
    renderPage();
    await scan('PS-042');

    const history = await screen.findByTestId('universal-scanner-history');
    await waitFor(() => expect(history).toHaveTextContent(/stint resolved/i));
    const badge = within(history).getByText('Project storage').closest('.mantine-Badge-root');
    expect(badge).not.toBeNull();
    expect((badge as HTMLElement).getAttribute('style')).toContain('--mantine-color-indigo');
  });
});
