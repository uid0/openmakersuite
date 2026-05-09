/**
 * Supplier List Page
 * Display and filter suppliers with item counts and tax-free status
 */
import {
    ActionIcon,
    Badge,
    Button,
    Group,
    Paper,
    Select,
    Stack,
    Table,
    Text,
    TextInput,
} from '@mantine/core';
import { IconEdit, IconExternalLink, IconEye, IconSearch } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { inventoryAPI } from '../services/api';
import { Supplier } from '../types';

const SupplierListPage: React.FC = () => {
  const navigate = useNavigate();
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedType, setSelectedType] = useState<string | null>(null);

  useEffect(() => {
    loadSuppliers();
  }, [selectedType]);

  const loadSuppliers = async () => {
    try {
      setLoading(true);
      const params: { supplier_type?: string; search?: string } = {};
      if (selectedType) {
        params.supplier_type = selectedType;
      }
      if (searchTerm) {
        params.search = searchTerm;
      }
      const response = await inventoryAPI.listSuppliers(params);
      setSuppliers(response.data.results);
    } catch (err) {
      console.error('Error loading suppliers:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Debounce search
    const timer = setTimeout(() => {
      loadSuppliers();
    }, 300);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  const filteredSuppliers = useMemo(() => {
    let filtered = [...suppliers];

    // Additional client-side filtering if needed
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(
        (supplier) =>
          supplier.name.toLowerCase().includes(term) ||
          (supplier.notes && supplier.notes.toLowerCase().includes(term))
      );
    }

    return filtered;
  }, [suppliers, searchTerm]);

  const getTypeBadgeColor = (type: string) => {
    switch (type) {
      case 'local':
        return 'blue';
      case 'online':
        return 'green';
      case 'national':
        return 'orange';
      default:
        return 'gray';
    }
  };

  const getTypeLabel = (type: string) => {
    switch (type) {
      case 'local':
        return 'Local';
      case 'online':
        return 'Online';
      case 'national':
        return 'National';
      default:
        return type;
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="supplier-list-page"
        hero={{ eyebrow: 'Inventory', title: 'Suppliers', description: 'Loading…' }}
      >
        <Paper p="md" withBorder>
          <Text c="dimmed">Loading suppliers…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="supplier-list-page"
      hero={{
        eyebrow: 'Inventory',
        title: 'Suppliers',
        description: `${filteredSuppliers.length} of ${suppliers.length} suppliers.`,
        action: (
          <Button onClick={() => navigate('/inventory/suppliers/new')}>Add new supplier</Button>
        ),
      }}
    >
      {/* Filters and Search */}
      <Paper p="md" withBorder>
        <Stack gap="md">
          <Group grow>
            <TextInput
              placeholder="Search by name or notes..."
              leftSection={<IconSearch size={16} />}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
            <Select
              placeholder="All Types"
              data={[
                { value: '', label: 'All Types' },
                { value: 'local', label: 'Local' },
                { value: 'online', label: 'Online' },
                { value: 'national', label: 'National' },
              ]}
              value={selectedType || ''}
              onChange={(value) => setSelectedType(value || null)}
              clearable
            />
          </Group>
        </Stack>
      </Paper>

      {/* Suppliers Table */}
      <Paper withBorder>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th>Items</Table.Th>
              <Table.Th>Tax-Free</Table.Th>
              <Table.Th>Website</Table.Th>
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filteredSuppliers.length === 0 ? (
              <Table.Tr>
                <Table.Td colSpan={6} style={{ textAlign: 'center', padding: '2rem' }}>
                  <Text c="dimmed">No suppliers found</Text>
                </Table.Td>
              </Table.Tr>
            ) : (
              filteredSuppliers.map((supplier) => (
                <Table.Tr key={supplier.id}>
                  <Table.Td>
                    <Text fw={500}>{supplier.name}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={getTypeBadgeColor(supplier.supplier_type)}>
                      {getTypeLabel(supplier.supplier_type)}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text
                      c="blue"
                      style={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/inventory/suppliers/${supplier.id}`)}
                    >
                      {supplier.item_count || 0} items
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    {supplier.tax_free_paperwork_filed ? (
                      <Badge color="green">Tax-Free</Badge>
                    ) : (
                      <Badge color="gray" variant="light">
                        Not Filed
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {supplier.website ? (
                      <Group gap="xs">
                        <Text size="sm" c="blue" truncate style={{ maxWidth: 200 }}>
                          {supplier.website}
                        </Text>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          onClick={() => window.open(supplier.website, '_blank')}
                        >
                          <IconExternalLink size={16} />
                        </ActionIcon>
                      </Group>
                    ) : (
                      <Text size="sm" c="dimmed">
                        -
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <ActionIcon
                        variant="subtle"
                        onClick={() => navigate(`/inventory/suppliers/${supplier.id}`)}
                      >
                        <IconEye size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="subtle"
                        onClick={() => navigate(`/inventory/suppliers/${supplier.id}/edit`)}
                      >
                        <IconEdit size={16} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))
            )}
          </Table.Tbody>
        </Table>
      </Paper>
    </WorkspacePage>
  );
};

export default SupplierListPage;
