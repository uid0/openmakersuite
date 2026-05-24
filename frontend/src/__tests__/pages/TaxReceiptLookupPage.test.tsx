/**
 * Tests for TaxReceiptLookupPage — closes the documented coverage gap for
 * the public donor receipt lookup flow (gh-457, AC-15 clear final state /
 * AC-19 actionable not-found state).
 *
 * The page is a public lookup-by-serial-number form: it must never list
 * other donors, must show an actionable "not found" message on bad input,
 * and must trigger a blob download for the receipt PDF on success.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import TaxReceiptLookupPage from '../../pages/TaxReceiptLookupPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <TaxReceiptLookupPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('TaxReceiptLookupPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders the public lookup form with only a serial-number input', () => {
    renderPage();

    expect(
      screen.getByRole('heading', { name: /tax receipt lookup/i, level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/serial number/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /lookup receipt/i })).toBeInTheDocument();
  });

  test('rejects an empty serial number without calling the API', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /lookup receipt/i }));

    await screen.findByText(/please enter a serial number/i);
    expect(api.donationsAPI.lookupTaxReceipt).not.toHaveBeenCalled();
  });

  test('renders the receipt detail block on a successful lookup', async () => {
    (api.donationsAPI.lookupTaxReceipt as jest.Mock).mockResolvedValue({
      data: {
        id: 'receipt-1',
        serial_number: 'RCP-001',
        donor_name: 'Alice Donor',
        donation_number: 'DN-7',
        issued_date: '2024-06-01T00:00:00Z',
      },
    });

    renderPage();

    fireEvent.change(screen.getByLabelText(/serial number/i), {
      target: { value: 'RCP-001' },
    });
    fireEvent.click(screen.getByRole('button', { name: /lookup receipt/i }));

    await screen.findByText(/receipt details/i);
    expect(screen.getByText(/alice donor/i)).toBeInTheDocument();
    expect(screen.getByText(/dn-7/i)).toBeInTheDocument();
    expect(api.donationsAPI.lookupTaxReceipt).toHaveBeenCalledWith('RCP-001');
  });

  test('shows the API-provided error message on a failed lookup', async () => {
    (api.donationsAPI.lookupTaxReceipt as jest.Mock).mockRejectedValue({
      response: { data: { error: 'No receipt with that serial number' } },
    });

    renderPage();

    fireEvent.change(screen.getByLabelText(/serial number/i), {
      target: { value: 'BAD-1' },
    });
    fireEvent.click(screen.getByRole('button', { name: /lookup receipt/i }));

    await screen.findByText(/no receipt with that serial number/i);
    expect(screen.queryByText(/receipt details/i)).not.toBeInTheDocument();
  });

  test('falls back to a default not-found message when the API gives no body', async () => {
    (api.donationsAPI.lookupTaxReceipt as jest.Mock).mockRejectedValue({});

    renderPage();

    fireEvent.change(screen.getByLabelText(/serial number/i), {
      target: { value: 'BAD-2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /lookup receipt/i }));

    await screen.findByText(/tax receipt not found/i);
  });

  test('downloading the clean receipt triggers the API and a download with the right name', async () => {
    (api.donationsAPI.lookupTaxReceipt as jest.Mock).mockResolvedValue({
      data: {
        id: 'receipt-1',
        serial_number: 'RCP-001',
        donor_name: 'Alice Donor',
        donation_number: 'DN-7',
        issued_date: '2024-06-01T00:00:00Z',
      },
    });
    (api.donationsAPI.downloadTaxReceipt as jest.Mock).mockResolvedValue({
      data: new Blob(['%PDF-1.4'], { type: 'application/pdf' }),
    });

    const createObjectURL = jest.fn().mockReturnValue('blob:fake-url');
    const revokeObjectURL = jest.fn();
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: createObjectURL,
      configurable: true,
    });
    Object.defineProperty(window.URL, 'revokeObjectURL', {
      value: revokeObjectURL,
      configurable: true,
    });

    renderPage();

    fireEvent.change(screen.getByLabelText(/serial number/i), {
      target: { value: 'RCP-001' },
    });
    fireEvent.click(screen.getByRole('button', { name: /lookup receipt/i }));

    await screen.findByText(/receipt details/i);
    fireEvent.click(screen.getByRole('button', { name: /download receipt \(clean\)/i }));

    await waitFor(() => {
      expect(api.donationsAPI.downloadTaxReceipt).toHaveBeenCalledWith('receipt-1', false);
    });
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake-url');
  });
});
