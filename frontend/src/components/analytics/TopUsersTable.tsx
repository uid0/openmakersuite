import { Card, Table, Text, Title } from '@mantine/core';
import React from 'react';

import type { AnalyticsTopUser } from '../../services/api';

interface TopUsersTableProps {
  users: AnalyticsTopUser[];
}

export const TopUsersTable: React.FC<TopUsersTableProps> = ({ users }) => (
  <Card withBorder p="md" radius="md" data-testid="top-users-table">
    <Title order={4}>Top contributors</Title>
    {users.length === 0 ? (
      <Text size="sm" c="dimmed" mt="sm">No completed work orders in this window.</Text>
    ) : (
      <Table mt="sm" striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Member</Table.Th>
            <Table.Th ta="right">Completed WOs</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {users.map((u) => (
            <Table.Tr key={u.user_id}>
              <Table.Td>{u.display_name}</Table.Td>
              <Table.Td ta="right">{u.completed_wo_count}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )}
  </Card>
);
