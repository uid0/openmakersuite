/**
 * Required-field messages under zod 4.
 *
 * zod 4 dropped zod 3's `required_error` param and ignores it at runtime, so a
 * schema still passing it shows zod's generic "Invalid option: expected one
 * of ..." / "Invalid input: expected number, received undefined" instead of
 * the message it asked for. These pin the intended message both at the schema
 * and on the supplier form, where a member clears the required Type select.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import SupplierFormPage from '../../pages/SupplierFormPage';
import * as api from '../../services/api';
import { requiredNumber, supplierSchema } from '../../utils/formSchemas';

vi.mock('../../services/api');

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => jest.fn(),
}));

const firstMessage = (result: { success: boolean; error?: { issues: { message: string }[] } }) =>
  result.error?.issues[0]?.message;

describe('requiredNumber', () => {
  test.each([undefined, null, ''])('an empty value (%j) is reported as required', (value) => {
    expect(firstMessage(requiredNumber.safeParse(value))).toBe('This field is required');
  });

  test('a value of the wrong type keeps zod’s own type message', () => {
    expect(firstMessage(requiredNumber.safeParse('abc'))).toMatch(/expected number/i);
  });

  test('a number passes', () => {
    expect(requiredNumber.safeParse(3).success).toBe(true);
  });
});

describe('supplierSchema.supplier_type', () => {
  const supplierType = supplierSchema.shape.supplier_type;

  test.each([undefined, null, ''])('an empty value (%j) is reported as required', (value) => {
    expect(firstMessage(supplierType.safeParse(value))).toBe('Supplier type is required');
  });

  test('an unknown type keeps zod’s own invalid-option message', () => {
    expect(firstMessage(supplierType.safeParse('bogus'))).toMatch(/invalid option/i);
  });
});

describe('SupplierFormPage — empty Type select', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('a member who clears the required Type sees "Supplier type is required"', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierFormPage />
        </MemoryRouter>
      </MantineProvider>,
    );

    fireEvent.change(screen.getByPlaceholderText('Supplier name'), {
      target: { value: 'Acme' },
    });
    // The form seeds Type as Local; picking the selected option again clears it.
    fireEvent.click(screen.getByPlaceholderText('Select supplier type'));
    fireEvent.click(await screen.findByRole('option', { name: 'Local' }));
    fireEvent.click(screen.getByText('Create Supplier'));

    expect(await screen.findByText('Supplier type is required')).toBeInTheDocument();
    expect(screen.queryByText(/invalid option/i)).not.toBeInTheDocument();
    expect(api.inventoryAPI.createSupplier).not.toHaveBeenCalled();
  });
});
