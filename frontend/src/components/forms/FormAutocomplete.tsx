/**
 * FormAutocomplete - Reusable autocomplete component with async data fetching
 * Supports suppliers, categories, and locations
 */
import { Autocomplete, AutocompleteProps, Loader } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useEffect, useMemo, useState } from 'react';
import { Control, Controller, FieldPath, FieldValues } from 'react-hook-form';
import { inventoryAPI } from '../../services/api';

export type AutocompleteType = 'suppliers' | 'categories' | 'locations';

export interface FormAutocompleteProps<T extends FieldValues> extends Omit<AutocompleteProps, 'name' | 'control' | 'data'> {
  name: FieldPath<T>;
  control: Control<T>;
  label?: string;
  description?: string;
  required?: boolean;
  autocompleteType?: AutocompleteType;
  data?: Array<{ value: string; label: string }>;
  fetchOnMount?: boolean;
  debounceMs?: number;
}

export function FormAutocomplete<T extends FieldValues>({
  name,
  control,
  label,
  description,
  required,
  autocompleteType,
  data: providedData,
  fetchOnMount = true,
  debounceMs = 300,
  ...autocompleteProps
}: FormAutocompleteProps<T>) {
  const [searchValue, setSearchValue] = useState('');
  const [debouncedSearch] = useDebouncedValue(searchValue, debounceMs);
  const [options, setOptions] = useState<Array<{ value: string; label: string }>>([]);
  const [loading, setLoading] = useState(false);

  // Fetch data based on autocompleteType
  useEffect(() => {
    if (!autocompleteType || providedData) return;

    const fetchData = async () => {
      setLoading(true);
      try {
        let response;
        switch (autocompleteType) {
          case 'suppliers':
            response = await inventoryAPI.listSuppliers();
            setOptions(
              response.data.results.map((supplier) => ({
                value: supplier.id.toString(),
                label: supplier.name,
              }))
            );
            break;
          case 'categories':
            response = await inventoryAPI.listCategories();
            setOptions(
              response.data.results.map((category) => ({
                value: category.id.toString(),
                label: category.name,
              }))
            );
            break;
          case 'locations':
            response = await inventoryAPI.listLocations();
            setOptions(
              response.data.results.map((location) => ({
                value: location.id.toString(),
                label: location.name,
              }))
            );
            break;
        }
      } catch (error) {
        console.error(`Error fetching ${autocompleteType}:`, error);
      } finally {
        setLoading(false);
      }
    };

    if (fetchOnMount) {
      fetchData();
    }
  }, [autocompleteType, fetchOnMount, providedData]);

  // Filter options based on search
  const filteredOptions = useMemo(() => {
    if (providedData) {
      return providedData.filter((option) =>
        option.label.toLowerCase().includes(debouncedSearch.toLowerCase())
      );
    }
    return options.filter((option) =>
      option.label.toLowerCase().includes(debouncedSearch.toLowerCase())
    );
  }, [options, debouncedSearch, providedData]);

  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => {
        const selectedOption = [...(providedData || options)].find(
          (opt) => opt.value === field.value?.toString()
        );

        return (
          <Autocomplete
            {...autocompleteProps}
            value={selectedOption?.label || searchValue || ''}
            onChange={(value) => {
              setSearchValue(value);
              const option = filteredOptions.find((opt) => opt.label === value);
              if (option) {
                field.onChange(option.value);
              } else {
                // User is typing, not selecting - update search but don't change field value
                // The field value will be set when an option is actually selected
              }
            }}
            data={filteredOptions}
            label={label}
            description={description}
            required={required}
            error={error?.message}
            rightSection={loading ? <Loader size="xs" /> : null}
            disabled={loading}
          />
        );
      }}
    />
  );
}
