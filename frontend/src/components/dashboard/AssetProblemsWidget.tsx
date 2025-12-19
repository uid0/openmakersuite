/**
 * Asset Problems Widget
 * Displays active asset problems (reported/in_progress)
 */
import { Badge, ScrollArea, Table, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { dashboardAPI } from '../../services/api';
import { AssetProblemsData } from '../../types';
import DashboardWidget from './DashboardWidget';

const AssetProblemsWidget: React.FC = () => {
  const [data, setData] = useState<AssetProblemsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await dashboardAPI.getAssetProblemsData();
      setData(response.data);
    } catch (err: any) {
      setError(err.response?.data?.error || 'Failed to load asset problems');
      console.error('Error loading asset problems:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleProblemClick = (assetId: string) => {
    navigate(`/assets/${assetId}`);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'reported':
        return 'red';
      case 'in_progress':
        return 'orange';
      default:
        return 'gray';
    }
  };

  return (
    <DashboardWidget title="Asset Problems" loading={loading} error={error || undefined}>
      {data && (
        <>
          <div style={{ marginBottom: 12 }}>
            <Badge size="lg" color="red" variant="light">
              {data.count} {data.count === 1 ? 'problem' : 'problems'}
            </Badge>
          </div>
          {data.problems.length > 0 ? (
            <ScrollArea h={200}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Asset</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Reported By</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.problems.map((problem) => (
                    <Table.Tr
                      key={problem.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => handleProblemClick(problem.asset_id)}
                    >
                      <Table.Td>
                        <Text size="sm" fw={500}>
                          {problem.asset_name}
                        </Text>
                        <Text size="xs" c="dimmed" lineClamp={1}>
                          {problem.description}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge color={getStatusColor(problem.status)} variant="light" size="sm">
                          {problem.status.replace('_', ' ')}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="dimmed">
                          {problem.reported_by || 'Unknown'}
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          ) : (
            <Text c="dimmed" size="sm" ta="center" py="xl">
              No active problems
            </Text>
          )}
        </>
      )}
    </DashboardWidget>
  );
};

export default AssetProblemsWidget;
