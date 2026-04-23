/**
 * Tests for dialog helpers (utils/dialogs.ts).
 */
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';

import {
  confirmAction,
  confirmDelete,
  promptInput,
  showError,
  showInfo,
  showSuccess,
} from '../../utils/dialogs';

jest.mock('@mantine/modals', () => ({
  modals: {
    open: jest.fn(),
    close: jest.fn(),
    openConfirmModal: jest.fn(),
  },
}));

jest.mock('@mantine/notifications', () => ({
  notifications: {
    show: jest.fn(),
  },
}));

const mockedModals = modals as jest.Mocked<typeof modals>;
const mockedNotifications = notifications as jest.Mocked<typeof notifications>;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('notification helpers', () => {
  it('showSuccess calls notifications.show with green color and default title', () => {
    showSuccess('Saved!');
    expect(mockedNotifications.show).toHaveBeenCalledWith({
      color: 'green',
      title: 'Success',
      message: 'Saved!',
    });
  });

  it('showSuccess accepts a custom title', () => {
    showSuccess('Saved!', 'All done');
    expect(mockedNotifications.show).toHaveBeenCalledWith({
      color: 'green',
      title: 'All done',
      message: 'Saved!',
    });
  });

  it('showError calls notifications.show with red color', () => {
    showError('Boom');
    expect(mockedNotifications.show).toHaveBeenCalledWith({
      color: 'red',
      title: 'Error',
      message: 'Boom',
    });
  });

  it('showInfo calls notifications.show without a color override', () => {
    showInfo('FYI');
    expect(mockedNotifications.show).toHaveBeenCalledWith({
      title: undefined,
      message: 'FYI',
    });
  });
});

describe('confirmDelete', () => {
  it('opens a red confirm modal and wires onConfirm', () => {
    const onConfirm = jest.fn();
    confirmDelete('Delete this widget?', onConfirm);

    expect(mockedModals.openConfirmModal).toHaveBeenCalledTimes(1);
    const payload = mockedModals.openConfirmModal.mock.calls[0][0];
    expect(payload.title).toBe('Confirm deletion');
    expect(payload.labels).toEqual({ confirm: 'Delete', cancel: 'Cancel' });
    expect(payload.confirmProps).toEqual({ color: 'red' });
    expect(payload.onConfirm).toBe(onConfirm);
    expect(onConfirm).not.toHaveBeenCalled();

    payload.onConfirm?.();
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe('confirmAction', () => {
  it('opens a confirm modal with default labels when none given', () => {
    const onConfirm = jest.fn();
    confirmAction('Proceed?', 'Are you sure?', onConfirm);

    const payload = mockedModals.openConfirmModal.mock.calls[0][0];
    expect(payload.title).toBe('Proceed?');
    expect(payload.labels).toEqual({ confirm: 'Confirm', cancel: 'Cancel' });
    expect(payload.confirmProps).toBeUndefined();
    expect(payload.onConfirm).toBe(onConfirm);
  });

  it('passes custom labels and confirm color through', () => {
    const onConfirm = jest.fn();
    confirmAction('Archive?', 'This hides the record.', onConfirm, {
      labels: { confirm: 'Archive', cancel: 'Keep' },
      color: 'orange',
    });

    const payload = mockedModals.openConfirmModal.mock.calls[0][0];
    expect(payload.labels).toEqual({ confirm: 'Archive', cancel: 'Keep' });
    expect(payload.confirmProps).toEqual({ color: 'orange' });
  });
});

describe('promptInput', () => {
  it('opens a modal with the given title and resolves null when closed without submit', async () => {
    const promise = promptInput('Rename', 'New name');

    expect(mockedModals.open).toHaveBeenCalledTimes(1);
    const payload = mockedModals.open.mock.calls[0][0];
    expect(payload.title).toBe('Rename');
    expect(typeof payload.modalId).toBe('string');

    payload.onClose?.();
    await expect(promise).resolves.toBeNull();
  });
});
