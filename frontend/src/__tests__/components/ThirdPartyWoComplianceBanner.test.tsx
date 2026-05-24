/**
 * Tests for the third-party WO compliance + warranty banner.
 */
import { render, screen, waitFor } from '@testing-library/react';
import ThirdPartyWoComplianceBanner from '../../components/ThirdPartyWoComplianceBanner';
import { thirdPartyMaintenanceAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  thirdPartyMaintenanceAPI: {
    getAssetWoStatus: jest.fn(),
  },
}));

const mockedApi = thirdPartyMaintenanceAPI as jest.Mocked<typeof thirdPartyMaintenanceAPI>;

describe('ThirdPartyWoComplianceBanner', () => {
  beforeEach(() => {
    mockedApi.getAssetWoStatus.mockReset();
  });

  it('renders nothing when no asset selected', () => {
    const { container } = render(<ThirdPartyWoComplianceBanner assetId={null} />);
    expect(container).toBeEmptyDOMElement();
    expect(mockedApi.getAssetWoStatus).not.toHaveBeenCalled();
  });

  it('renders warranty active banner', async () => {
    mockedApi.getAssetWoStatus.mockResolvedValueOnce({
      data: {
        asset_id: 'a',
        warranty: {
          id: 'w1',
          asset: 'a',
          install_date: '2026-01-01',
          duration_days: 365,
          end_date: '2027-01-01',
          provider: 'DeWalt',
          policy_number: 'POL-1',
          notes: '',
          is_active: true,
          created_at: '',
          updated_at: '',
        },
        warranty_recovery_recommended: true,
      },
    } as any);

    render(<ThirdPartyWoComplianceBanner assetId="a" />);

    await waitFor(() => {
      expect(screen.getByText(/Warranty active/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/DeWalt/)).toBeInTheDocument();
    expect(screen.getByText(/POL-1/)).toBeInTheDocument();
  });

  it('renders nothing when no warranty and no vendor compliance issues', async () => {
    mockedApi.getAssetWoStatus.mockResolvedValueOnce({
      data: {
        asset_id: 'a',
        warranty: null,
        warranty_recovery_recommended: false,
      },
    } as any);

    render(<ThirdPartyWoComplianceBanner assetId="a" />);
    await waitFor(() => {
      expect(mockedApi.getAssetWoStatus).toHaveBeenCalled();
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('renders TDLR expiring + COI expired banners', async () => {
    mockedApi.getAssetWoStatus.mockResolvedValueOnce({
      data: {
        asset_id: 'a',
        warranty: null,
        warranty_recovery_recommended: false,
        vendor_compliance: {
          vendor_id: 'v',
          vendor_name: 'ACME',
          is_active: true,
          tdlr_status: 'expiring_soon',
          tdlr_expires_at: '2026-05-15',
          coi_status: 'expired',
          coi_expires_at: '2026-04-01',
        },
      },
    } as any);

    render(<ThirdPartyWoComplianceBanner assetId="a" vendorId="v" />);

    await waitFor(() => {
      expect(screen.getByText(/TDLR license:/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Expiring within 30 days/)).toBeInTheDocument();
    expect(screen.getByText(/COI:/i)).toBeInTheDocument();
    expect(screen.getByText(/Expired/)).toBeInTheDocument();
    expect(mockedApi.getAssetWoStatus).toHaveBeenCalledWith('a', 'v');
  });

  it('renders error message on failure', async () => {
    mockedApi.getAssetWoStatus.mockRejectedValueOnce({
      response: { data: { detail: 'boom' } },
    });

    render(<ThirdPartyWoComplianceBanner assetId="a" />);
    await waitFor(() => {
      expect(screen.getByText('boom')).toBeInTheDocument();
    });
  });
});
