/**
 * Tests for RoomOperationalModeControl (epic ga-72l): rendering the current
 * room mode + legend, creating a mode when none exists, updating an existing
 * one, and the staff-only gating.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import RoomOperationalModeControl from '../../components/RoomOperationalModeControl';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      ...(actual as any).forgekeyAPI,
      listRoomOperationalModes: jest.fn(),
      createRoomOperationalMode: jest.fn(),
      setRoomOperationalMode: jest.fn(),
    },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildMode = (overrides: Partial<any> = {}) => ({
  id: 7,
  location: 5,
  location_name: 'Wood Shop',
  mode: 'available',
  updated_by: 2,
  updated_by_username: 'bob',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const renderControl = () =>
  render(
    <MantineProvider env="test">
      <RoomOperationalModeControl locationId={5} locationName="Wood Shop" />
    </MantineProvider>,
  );

const pickMode = async (label: string) => {
  // Mantine 9.4 gives the eagerly-rendered listbox an accessible name via
  // aria-labelledby, so 'Set mode' now matches both the input and the listbox.
  // Scope to the input element to keep targeting the Select control.
  fireEvent.click(screen.getByLabelText('Set mode', { selector: 'input' }));
  fireEvent.click(await screen.findByRole('option', { name: label }));
};

describe('RoomOperationalModeControl', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.setItem('is_staff', 'true');
  });

  afterEach(() => {
    localStorage.removeItem('is_staff');
    localStorage.removeItem('is_superuser');
  });

  it('renders the current mode and the legend', async () => {
    mockApi.listRoomOperationalModes.mockResolvedValue({
      data: [buildMode({ mode: 'locked_out' })],
    } as any);
    renderControl();

    await screen.findByTestId('room-operational-mode');
    expect(screen.getByTestId('room-mode-current')).toHaveTextContent('Locked out');
    expect(screen.getByTestId('room-mode-swatch')).toBeInTheDocument();
    expect(screen.getByTestId('indicator-legend')).toBeInTheDocument();
  });

  it('creates a room mode when none exists yet', async () => {
    mockApi.listRoomOperationalModes.mockResolvedValue({ data: [] } as any);
    mockApi.createRoomOperationalMode.mockResolvedValue({} as any);
    renderControl();

    await screen.findByTestId('room-operational-mode');
    await pickMode('In use');
    fireEvent.click(screen.getByTestId('room-mode-save'));

    await waitFor(() =>
      expect(mockApi.createRoomOperationalMode).toHaveBeenCalledWith({
        location: 5,
        mode: 'in_use',
      }),
    );
  });

  it('updates an existing room mode', async () => {
    mockApi.listRoomOperationalModes.mockResolvedValue({ data: [buildMode()] } as any);
    mockApi.setRoomOperationalMode.mockResolvedValue({} as any);
    renderControl();

    await screen.findByTestId('room-operational-mode');
    await pickMode('In use for a class');
    fireEvent.click(screen.getByTestId('room-mode-save'));

    await waitFor(() =>
      expect(mockApi.setRoomOperationalMode).toHaveBeenCalledWith(7, 'classroom'),
    );
  });

  it('disables editing for non-staff users', async () => {
    localStorage.removeItem('is_staff');
    mockApi.listRoomOperationalModes.mockResolvedValue({ data: [buildMode()] } as any);
    renderControl();

    await screen.findByTestId('room-operational-mode');
    expect(screen.getByTestId('room-mode-save')).toBeDisabled();
    expect(
      screen.getByText('Staff access is required to change the room mode.'),
    ).toBeInTheDocument();
  });
});
