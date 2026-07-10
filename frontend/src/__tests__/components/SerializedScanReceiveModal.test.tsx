/**
 * Tests for SerializedScanReceiveModal (op-n4pi) — the scan-driven batch
 * receive surface. Covers: each Enter calls scan_receive against the batch
 * item/lot/expiry; new-vs-duplicate is surfaced from the `created` flag;
 * undo-last disposes the most recent newly-received unit; backend errors show
 * inline; and closing a non-empty batch notifies the launcher to refresh.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';

import SerializedScanReceiveModal from '../../components/inventory/SerializedScanReceiveModal';
import { serializedComponentsAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    serializedComponentsAPI: {
      scanReceive: jest.fn(),
      dispose: jest.fn(),
    },
  };
});

const mockAPI = serializedComponentsAPI as jest.Mocked<typeof serializedComponentsAPI>;

const ITEM = { id: 'item-1', name: 'Cutting blade' };

const scanResult = (overrides: { id?: string; serial_number: string; created: boolean }) =>
  ({
    data: {
      id: overrides.id ?? 'unit-1',
      item: ITEM.id,
      serial_number: overrides.serial_number,
      lot: '',
      expiration_date: null,
      status: 'in_stock',
      created: overrides.created,
    },
  }) as never;

const renderModal = (props: Partial<React.ComponentProps<typeof SerializedScanReceiveModal>> = {}) => {
  const onClose = props.onClose ?? vi.fn();
  const onReceived = props.onReceived ?? vi.fn();
  render(
    <MantineProvider>
      <SerializedScanReceiveModal
        opened
        onClose={onClose}
        item={ITEM}
        onReceived={onReceived}
        {...props}
      />
    </MantineProvider>,
  );
  return { onClose, onReceived };
};

const scan = (serial: string) => {
  fireEvent.change(screen.getByTestId('scan-receive-serial'), {
    target: { value: serial },
  });
  fireEvent.submit(screen.getByTestId('scan-receive-form'));
};

describe('SerializedScanReceiveModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('receives a scanned serial against the batch item and marks it new', async () => {
    mockAPI.scanReceive.mockResolvedValue(scanResult({ serial_number: 'SN-1', created: true }));
    renderModal();

    scan('SN-1');

    await waitFor(() =>
      expect(mockAPI.scanReceive).toHaveBeenCalledWith({
        item: 'item-1',
        serial_number: 'SN-1',
        lot: undefined,
        expiration_date: undefined,
      }),
    );

    const log = await screen.findByTestId('scan-receive-log');
    expect(within(log).getByText('SN-1')).toBeInTheDocument();
    expect(within(log).getByText('New')).toBeInTheDocument();
    expect(screen.getByTestId('scan-receive-new-count')).toHaveTextContent('1 new');
    // Input is cleared for the next scan.
    expect(screen.getByTestId('scan-receive-serial')).toHaveValue('');
  });

  it('applies the batch lot to every scan', async () => {
    mockAPI.scanReceive.mockResolvedValue(scanResult({ serial_number: 'SN-1', created: true }));
    renderModal();

    fireEvent.change(screen.getByTestId('scan-receive-lot'), { target: { value: 'LOT-9' } });
    scan('SN-1');

    await waitFor(() =>
      expect(mockAPI.scanReceive).toHaveBeenCalledWith(
        expect.objectContaining({ item: 'item-1', serial_number: 'SN-1', lot: 'LOT-9' }),
      ),
    );
  });

  it('flags an idempotent re-scan as a duplicate rather than an error', async () => {
    mockAPI.scanReceive.mockResolvedValue(scanResult({ serial_number: 'SN-DUP', created: false }));
    renderModal();

    scan('SN-DUP');

    const log = await screen.findByTestId('scan-receive-log');
    expect(await within(log).findByText('Duplicate')).toBeInTheDocument();
    expect(screen.getByTestId('scan-receive-dup-count')).toHaveTextContent('1 duplicate');
    expect(screen.getByTestId('scan-receive-new-count')).toHaveTextContent('0 new');
  });

  it('undoes the last newly-received unit by disposing it', async () => {
    mockAPI.scanReceive.mockResolvedValue(
      scanResult({ id: 'unit-77', serial_number: 'SN-7', created: true }),
    );
    mockAPI.dispose.mockResolvedValue({ data: { id: 'unit-77' } } as never);
    renderModal();

    scan('SN-7');
    await screen.findByText('SN-7');

    fireEvent.click(screen.getByTestId('scan-receive-undo'));

    await waitFor(() =>
      expect(mockAPI.dispose).toHaveBeenCalledWith('unit-77', {
        disposal_reason: 'Undo scan-receive (batch)',
      }),
    );
    // The entry is struck through / marked undone and the new-count drops to 0.
    expect(await screen.findByText('Undone')).toBeInTheDocument();
    expect(screen.getByTestId('scan-receive-new-count')).toHaveTextContent('0 new');
  });

  it('does not offer undo when nothing new has been received', async () => {
    renderModal();
    expect(screen.getByTestId('scan-receive-undo')).toBeDisabled();
  });

  it('surfaces a backend error inline', async () => {
    mockAPI.scanReceive.mockRejectedValue({
      response: { data: { detail: 'Item is not serialized.' } },
    });
    renderModal();

    scan('SN-BAD');

    expect(await screen.findByTestId('scan-receive-error')).toHaveTextContent(
      'Item is not serialized.',
    );
  });

  it('notifies the launcher to refresh once on close when the batch received something', async () => {
    mockAPI.scanReceive.mockResolvedValue(scanResult({ serial_number: 'SN-1', created: true }));
    const { onClose, onReceived } = renderModal();

    scan('SN-1');
    await screen.findByText('SN-1');

    fireEvent.click(screen.getByTestId('scan-receive-done'));

    expect(onReceived).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not refresh on close when nothing was received', async () => {
    const { onClose, onReceived } = renderModal();

    fireEvent.click(screen.getByTestId('scan-receive-done'));

    expect(onReceived).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
