/**
 * Tests for the ForgeKey certificates (PKI) page.
 *
 * Covers: non-staff redirect, the active-CA + device-cert read views, and the
 * confirm-then-rotate CA flow.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyCertificatesPage from '../../pages/ForgeKeyCertificatesPage';
import { forgekeyAPI } from '../../services/api';
import { showSuccess } from '../../utils/dialogs';

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
      listCertificateAuthorities: jest.fn(),
      listDeviceCertificates: jest.fn(),
      rotateCA: jest.fn(),
    },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildCA = (overrides: Partial<any> = {}) => ({
  id: 'ca1',
  name: 'forgekey-root',
  common_name: 'ForgeKey Internal Root CA',
  fingerprint_sha256: 'a'.repeat(64),
  not_before: '2026-01-01T00:00:00Z',
  not_after: '2036-01-01T00:00:00Z',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  active_cert_count: 2,
  revoked_cert_count: 1,
  ...overrides,
});

const buildCert = (overrides: Partial<any> = {}) => ({
  id: 'dc1',
  device: 'di1',
  device_chip_id: 'chip-aabbcc',
  serial: '0A',
  subject: 'CN=device',
  fingerprint_sha256: 'b'.repeat(64),
  not_before: '2026-01-01T00:00:00Z',
  not_after: '2027-01-01T00:00:00Z',
  revoked_at: null,
  issued_by: 'enrollment',
  created_at: '2026-01-01T00:00:00Z',
  status: 'active',
  ...overrides,
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/forgekey-certificates']}>
        <Routes>
          <Route
            path="/facilities/forgekey-certificates"
            element={<ForgeKeyCertificatesPage />}
          />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyCertificatesPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    mockApi.listCertificateAuthorities.mockResolvedValue({ data: [buildCA()] } as any);
    mockApi.listDeviceCertificates.mockResolvedValue({ data: [buildCert()] } as any);
  });

  test('non-staff users are redirected', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockApi.listCertificateAuthorities).not.toHaveBeenCalled();
  });

  test('staff sees the active CA + device certificates', async () => {
    localStorage.setItem('is_staff', 'true');

    renderPage();

    expect(await screen.findByText('ForgeKey Internal Root CA')).toBeInTheDocument();
    expect(screen.getByText('a'.repeat(64))).toBeInTheDocument();
    expect(screen.getByTestId('cert-dc1')).toBeInTheDocument();
    expect(screen.getByText('chip-aabbcc')).toBeInTheDocument();
  });

  test('rotating the CA confirms then calls rotateCA', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.rotateCA.mockResolvedValue({ data: buildCA({ id: 'ca2' }) } as any);

    renderPage();

    fireEvent.click(await screen.findByTestId('rotate-ca'));
    fireEvent.click(await screen.findByTestId('rotate-ca-confirm'));

    await waitFor(() => expect(mockApi.rotateCA).toHaveBeenCalled());
    await waitFor(() => expect(showSuccess).toHaveBeenCalled());
  });
});
