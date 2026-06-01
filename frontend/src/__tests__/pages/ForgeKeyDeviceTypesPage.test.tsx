/**
 * Tests for the ForgeKey device-types catalog page.
 *
 * Covers: non-staff redirect, the catalog table, the edit→save path, and the
 * new-type modal opening.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyDeviceTypesPage from '../../pages/ForgeKeyDeviceTypesPage';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  showInfo: jest.fn(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      ...(actual as any).forgekeyAPI,
      listDeviceTypes: jest.fn(),
      createDeviceType: jest.fn(),
      updateDeviceType: jest.fn(),
    },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildType = (overrides: Partial<any> = {}) => ({
  id: 1,
  name: 'Indicator',
  code: 'indicator',
  description: 'status light',
  is_active: true,
  ...overrides,
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/forgekey-device-types']}>
        <Routes>
          <Route
            path="/facilities/forgekey-device-types"
            element={<ForgeKeyDeviceTypesPage />}
          />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyDeviceTypesPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    mockApi.listDeviceTypes.mockResolvedValue({ data: [buildType()] } as any);
  });

  test('non-staff users are redirected', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockApi.listDeviceTypes).not.toHaveBeenCalled();
  });

  test('staff sees the device-type catalog', async () => {
    localStorage.setItem('is_staff', 'true');

    renderPage();

    expect(await screen.findByText('Indicator')).toBeInTheDocument();
    expect(screen.getByText('indicator')).toBeInTheDocument();
    expect(screen.getByTestId('type-1')).toBeInTheDocument();
  });

  test('editing a type saves via updateDeviceType', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.updateDeviceType.mockResolvedValue({ data: buildType() } as any);

    renderPage();

    fireEvent.click(await screen.findByTestId('edit-1'));
    fireEvent.click(await screen.findByTestId('save-type'));

    await waitFor(() =>
      expect(mockApi.updateDeviceType).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ name: 'Indicator' }),
      ),
    );
  });

  test('the New type button opens the create modal', async () => {
    localStorage.setItem('is_staff', 'true');

    renderPage();

    fireEvent.click(await screen.findByTestId('new-type'));

    expect(await screen.findByText('New device type')).toBeInTheDocument();
  });
});
