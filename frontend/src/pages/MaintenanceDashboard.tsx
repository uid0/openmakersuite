import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Container,
  Divider,
  FileButton,
  Group,
  List,
  Loader,
  Modal,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCalendar,
  IconCalendarEvent,
  IconCheck,
  IconClipboardList,
  IconFileText,
  IconPlus,
  IconRefresh,
  IconUpload,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { maintenanceAPI, reorderAPI, workOrderAPI } from '../services/api';
import { LowStockAlert, MaintenanceItem, WorkOrder, WorkOrderUploadResult } from '../types';

const STATUS_COLORS: Record<string, string> = {
  open: 'blue',
  in_progress: 'yellow',
  blocked: 'red',
  completed: 'green',
};

const STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  in_progress: 'In Progress',
  blocked: 'Blocked',
  completed: 'Completed',
};

interface StatCardProps {
  title: string;
  value: number;
  icon: React.ReactNode;
  color: string;
  description?: string;
}

const StatCard: React.FC<StatCardProps> = ({ title, value, icon, color, description }) => (
  <Card withBorder p="md" radius="md">
    <Group justify="space-between" mb="xs">
      <Text size="sm" c="dimmed" fw={500}>{title}</Text>
      <Box c={color}>{icon}</Box>
    </Group>
    <Text size="2rem" fw={700} c={color}>{value}</Text>
    {description && <Text size="xs" c="dimmed" mt={4}>{description}</Text>}
  </Card>
);

interface MaintenanceItemRowProps {
  item: MaintenanceItem;
  onGenerateWorkOrder: (item: MaintenanceItem) => void;
  generating: boolean;
}

const MaintenanceItemRow: React.FC<MaintenanceItemRowProps> = ({
  item,
  onGenerateWorkOrder,
  generating,
}) => {
  const daysOverdue = item.days_overdue;
  const nextDue = item.next_due_at ? new Date(item.next_due_at) : null;
  const daysUntilDue = nextDue
    ? Math.ceil((nextDue.getTime() - Date.now()) / (1000 * 60 * 60 * 24))
    : null;

  return (
    <Card
      withBorder
      p="sm"
      radius="sm"
      mb="xs"
      style={{ borderLeft: `4px solid ${item.is_overdue ? '#fa5252' : '#fd7e14'}` }}
    >
      <Group justify="space-between" wrap="nowrap">
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Group gap="xs" mb={4}>
            <Text fw={600} size="sm" truncate>
              {item.title}
            </Text>
            {item.is_overdue && (
              <Badge color="red" size="xs">
                {daysOverdue != null ? `${daysOverdue}d overdue` : 'Overdue'}
              </Badge>
            )}
            {!item.is_overdue && daysUntilDue != null && (
              <Badge color="orange" variant="light" size="xs">
                Due in {daysUntilDue}d
              </Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed">
            {item.asset_name}
            {item.asset_tag && ` · ${item.asset_tag}`}
          </Text>
          {item.materials.length > 0 && (
            <Text size="xs" c="dimmed">
              {item.materials.length} material{item.materials.length !== 1 ? 's' : ''} needed
            </Text>
          )}
        </Box>
        <Group gap="xs" wrap="nowrap">
          <Tooltip label="Generate Work Order">
            <ActionIcon
              variant="light"
              color="blue"
              size="md"
              onClick={() => onGenerateWorkOrder(item)}
              loading={generating}
              aria-label="Generate Work Order"
            >
              <IconPlus size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="View Asset">
            <ActionIcon
              variant="light"
              color="gray"
              size="md"
              component={Link}
              to={`/assets/${item.asset}`}
            >
              <IconClipboardList size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
    </Card>
  );
};

const WorkOrderRow: React.FC<{ wo: WorkOrder }> = ({ wo }) => {
  const dueDate = wo.due_date ? new Date(wo.due_date) : null;
  const apiBaseUrl = process.env.REACT_APP_API_URL || '';

  return (
    <Card
      withBorder
      p="sm"
      radius="sm"
      mb="xs"
      style={{
        borderLeft: `4px solid ${wo.is_overdue ? '#fa5252' : '#228be6'}`,
      }}
    >
      <Group justify="space-between" wrap="nowrap">
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Group gap="xs" mb={4}>
            <Text fw={600} size="sm" truncate>
              {wo.short_id}
            </Text>
            <Badge color={STATUS_COLORS[wo.status] || 'gray'} size="xs">
              {STATUS_LABELS[wo.status] || wo.status}
            </Badge>
            {wo.is_overdue && (
              <Badge color="red" size="xs">Overdue</Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed" truncate>
            {wo.maintenance_item_title} · {wo.asset_name}
          </Text>
          {dueDate && (
            <Text size="xs" c="dimmed">
              Due: {dueDate.toLocaleDateString()}
            </Text>
          )}
          {wo.task_total_count != null && wo.task_total_count > 0 && (
            <Text size="xs" c="dimmed">
              Tasks: {wo.task_completion_count}/{wo.task_total_count} completed
            </Text>
          )}
        </Box>
        <Group gap="xs" wrap="nowrap">
          <Tooltip label="Open Work Order">
            <ActionIcon
              variant="light"
              color="blue"
              size="md"
              component={Link}
              to={`/maintenance/work-orders/${wo.id}`}
            >
              <IconClipboardList size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Print PDF">
            <ActionIcon
              variant="light"
              color="gray"
              size="md"
              component="a"
              href={`${apiBaseUrl}/inventory/work-orders/${wo.id}/pdf/`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <IconFileText size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
    </Card>
  );
};

const MaintenanceDashboard: React.FC = () => {
  const [overdueItems, setOverdueItems] = useState<MaintenanceItem[]>([]);
  const [dueThisWeek, setDueThisWeek] = useState<MaintenanceItem[]>([]);
  const [dueThisMonth, setDueThisMonth] = useState<MaintenanceItem[]>([]);
  const [openWorkOrders, setOpenWorkOrders] = useState<WorkOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [generatingBulk, setGeneratingBulk] = useState(false);
  const [generatingFor, setGeneratingFor] = useState<string | null>(null);
  const [bulkConfirmOpened, { open: openBulkConfirm, close: closeBulkConfirm }] = useDisclosure(false);
  const [stockWarning, setStockWarning] = useState<{
    item: MaintenanceItem;
    alerts: LowStockAlert[];
  } | null>(null);
  const [reordering, setReordering] = useState(false);
  const [isStaff, setIsStaff] = useState(false);
  const [uploadingPdf, setUploadingPdf] = useState(false);
  const [uploadResult, setUploadResult] = useState<WorkOrderUploadResult | null>(null);
  const [uploadResultOpened, { open: openUploadResult, close: closeUploadResult }] =
    useDisclosure(false);
  const resetPdfRef = useRef<() => void>(null);

  useEffect(() => {
    setIsStaff(localStorage.getItem('is_staff') === 'true');
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [weekRes, monthRes, ordersRes] = await Promise.all([
        workOrderAPI.getDueThisWeek(),
        workOrderAPI.getDueThisMonth(),
        workOrderAPI.listWorkOrders({ status: 'open' }),
      ]);

      const weekItems: MaintenanceItem[] = Array.isArray(weekRes.data)
        ? weekRes.data
        : (weekRes.data as any).results || [];
      const monthItems: MaintenanceItem[] = Array.isArray(monthRes.data)
        ? monthRes.data
        : (monthRes.data as any).results || [];
      const orders: WorkOrder[] = Array.isArray(ordersRes.data)
        ? ordersRes.data
        : (ordersRes.data as any).results || [];

      const overdue = weekItems.filter((i) => i.is_overdue);
      const thisWeekOnly = weekItems.filter(
        (i) => !i.is_overdue && monthItems.some((m) => m.id === i.id)
      );
      const thisMonthOnly = monthItems.filter(
        (m) => !weekItems.some((w) => w.id === m.id)
      );

      setOverdueItems(overdue);
      setDueThisWeek(weekItems.filter((i) => !i.is_overdue));
      setDueThisMonth(thisMonthOnly);
      setOpenWorkOrders(orders);
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to load maintenance data.',
        color: 'red',
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const createWorkOrder = async (item: MaintenanceItem) => {
    setGeneratingFor(item.id);
    try {
      await workOrderAPI.generateWorkOrder(item.id);
      notifications.show({
        title: 'Work Order Created',
        message: `Work order created for: ${item.title}`,
        color: 'green',
        icon: <IconCheck size={16} />,
      });
      loadData();
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to create work order.',
        color: 'red',
      });
    } finally {
      setGeneratingFor(null);
    }
  };

  const handleGenerateWorkOrder = async (item: MaintenanceItem) => {
    setGeneratingFor(item.id);
    try {
      const { data } = await workOrderAPI.checkMaterialStock(item.id);
      if (data.low_stock_alerts.length > 0) {
        setStockWarning({ item, alerts: data.low_stock_alerts });
        setGeneratingFor(null);
        return;
      }
    } catch {
      // Stock check is advisory; fall through and generate anyway.
    }
    await createWorkOrder(item);
  };

  const handleReorderAndGenerate = async () => {
    if (!stockWarning) return;
    const { item, alerts } = stockWarning;
    setReordering(true);
    try {
      await Promise.all(
        alerts.map((alert) =>
          reorderAPI.createRequest({
            item: alert.item_id,
            quantity: alert.reorder_qty,
            request_notes: `Auto-created from low-stock warning on WO for: ${item.title}`,
          })
        )
      );
      notifications.show({
        title: 'Reorder Requests Created',
        message: `Created ${alerts.length} reorder request(s).`,
        color: 'green',
        icon: <IconCheck size={16} />,
      });
    } catch {
      notifications.show({
        title: 'Reorder Failed',
        message: 'Failed to create one or more reorder requests.',
        color: 'red',
      });
    } finally {
      setReordering(false);
      setStockWarning(null);
      await createWorkOrder(item);
    }
  };

  const handleGenerateAnyway = async () => {
    if (!stockWarning) return;
    const { item } = stockWarning;
    setStockWarning(null);
    await createWorkOrder(item);
  };

  const handlePdfUpload = async (file: File | null) => {
    if (!file) return;
    setUploadingPdf(true);
    try {
      const { data } = await workOrderAPI.uploadPdf(file);
      setUploadResult(data);
      openUploadResult();
      if (data.status === 'applied') {
        notifications.show({
          title: 'Work Order Uploaded',
          message: data.completed_items.length
            ? `Marked ${data.completed_items.length} step(s) complete.`
            : 'Submission applied to work order.',
          color: 'green',
          icon: <IconCheck size={16} />,
        });
        loadData();
      } else {
        notifications.show({
          title: 'Upload Failed to Apply',
          message: data.errors[0] || 'PDF could not be applied to a work order.',
          color: 'red',
        });
      }
    } catch (err: any) {
      notifications.show({
        title: 'Upload Failed',
        message: err?.response?.data?.detail || 'Could not upload PDF.',
        color: 'red',
      });
    } finally {
      setUploadingPdf(false);
      resetPdfRef.current?.();
    }
  };

  const handleBulkGenerate = async () => {
    closeBulkConfirm();
    setGeneratingBulk(true);
    try {
      const res = await workOrderAPI.generateBulkWorkOrders();
      notifications.show({
        title: 'Work Orders Generated',
        message: `Created ${res.data.created} work order(s).`,
        color: 'green',
        icon: <IconCheck size={16} />,
      });
      loadData();
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to generate work orders.',
        color: 'red',
      });
    } finally {
      setGeneratingBulk(false);
    }
  };

  if (loading) {
    return (
      <Container py="xl">
        <Group justify="center">
          <Loader />
          <Text c="dimmed">Loading maintenance data…</Text>
        </Group>
      </Container>
    );
  }

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Preventive Maintenance</Title>
        <Group>
          <Button
            leftSection={<IconRefresh size={16} />}
            variant="default"
            size="sm"
            onClick={loadData}
            loading={loading}
          >
            Refresh
          </Button>
          <Button
            leftSection={<IconPlus size={16} />}
            size="sm"
            onClick={openBulkConfirm}
            loading={generatingBulk}
          >
            Generate Due Work Orders
          </Button>
          {isStaff && (
            <FileButton
              resetRef={resetPdfRef}
              accept="application/pdf"
              onChange={handlePdfUpload}
            >
              {(props) => (
                <Button
                  {...props}
                  leftSection={<IconUpload size={16} />}
                  variant="default"
                  size="sm"
                  loading={uploadingPdf}
                  aria-label="Upload completed work order PDF"
                >
                  Upload PDF
                </Button>
              )}
            </FileButton>
          )}
        </Group>
      </Group>

      {/* Stats row */}
      <SimpleGrid cols={{ base: 2, sm: 4 }} mb="lg">
        <StatCard
          title="Overdue"
          value={overdueItems.length}
          icon={<IconAlertTriangle size={20} />}
          color={overdueItems.length > 0 ? 'red' : 'green'}
          description="Past due date"
        />
        <StatCard
          title="Due This Week"
          value={dueThisWeek.length}
          icon={<IconCalendar size={20} />}
          color="orange"
          description="Next 7 days"
        />
        <StatCard
          title="Due This Month"
          value={dueThisMonth.length}
          icon={<IconCalendarEvent size={20} />}
          color="blue"
          description="Next 30 days"
        />
        <StatCard
          title="Open Work Orders"
          value={openWorkOrders.length}
          icon={<IconClipboardList size={20} />}
          color="teal"
          description="Awaiting completion"
        />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        {/* Overdue Items */}
        <Box>
          <Group mb="sm">
            <IconAlertTriangle size={18} color="red" />
            <Title order={4} c="red">Overdue Tasks</Title>
            <Badge color="red">{overdueItems.length}</Badge>
          </Group>
          {overdueItems.length === 0 ? (
            <Card withBorder p="md" radius="sm">
              <Group>
                <IconCheck size={16} color="green" />
                <Text size="sm" c="dimmed">No overdue tasks — great work!</Text>
              </Group>
            </Card>
          ) : (
            <Stack gap={0}>
              {overdueItems.map((item) => (
                <MaintenanceItemRow
                  key={item.id}
                  item={item}
                  onGenerateWorkOrder={handleGenerateWorkOrder}
                  generating={generatingFor === item.id}
                />
              ))}
            </Stack>
          )}
        </Box>

        {/* Due This Week */}
        <Box>
          <Group mb="sm">
            <IconCalendar size={18} color="orange" />
            <Title order={4} c="orange">Due This Week</Title>
            <Badge color="orange">{dueThisWeek.length}</Badge>
          </Group>
          {dueThisWeek.length === 0 ? (
            <Card withBorder p="md" radius="sm">
              <Text size="sm" c="dimmed">No tasks due this week.</Text>
            </Card>
          ) : (
            <Stack gap={0}>
              {dueThisWeek.map((item) => (
                <MaintenanceItemRow
                  key={item.id}
                  item={item}
                  onGenerateWorkOrder={handleGenerateWorkOrder}
                  generating={generatingFor === item.id}
                />
              ))}
            </Stack>
          )}
        </Box>

        {/* Open Work Orders */}
        <Box>
          <Group mb="sm">
            <IconClipboardList size={18} color="teal" />
            <Title order={4}>Open Work Orders</Title>
            <Badge color="teal">{openWorkOrders.length}</Badge>
          </Group>
          {openWorkOrders.length === 0 ? (
            <Card withBorder p="md" radius="sm">
              <Text size="sm" c="dimmed">No open work orders.</Text>
            </Card>
          ) : (
            <Stack gap={0}>
              {openWorkOrders.slice(0, 10).map((wo) => (
                <WorkOrderRow key={wo.id} wo={wo} />
              ))}
              {openWorkOrders.length > 10 && (
                <Text size="xs" c="dimmed" ta="center" mt="xs">
                  +{openWorkOrders.length - 10} more
                </Text>
              )}
            </Stack>
          )}
        </Box>

        {/* Due This Month */}
        <Box>
          <Group mb="sm">
            <IconCalendarEvent size={18} color="blue" />
            <Title order={4} c="blue">Due This Month</Title>
            <Badge color="blue">{dueThisMonth.length}</Badge>
          </Group>
          {dueThisMonth.length === 0 ? (
            <Card withBorder p="md" radius="sm">
              <Text size="sm" c="dimmed">No additional tasks due this month.</Text>
            </Card>
          ) : (
            <Stack gap={0}>
              {dueThisMonth.map((item) => (
                <MaintenanceItemRow
                  key={item.id}
                  item={item}
                  onGenerateWorkOrder={handleGenerateWorkOrder}
                  generating={generatingFor === item.id}
                />
              ))}
            </Stack>
          )}
        </Box>
      </SimpleGrid>

      {/* Bulk Generate Confirmation Modal */}
      <Modal
        opened={bulkConfirmOpened}
        onClose={closeBulkConfirm}
        title="Generate Work Orders"
        size="sm"
      >
        <Text size="sm" mb="md">
          This will create work orders for all overdue and due-this-week maintenance tasks
          that don't already have an open work order. Continue?
        </Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={closeBulkConfirm}>Cancel</Button>
          <Button color="blue" onClick={handleBulkGenerate}>Generate</Button>
        </Group>
      </Modal>

      {/* Low-stock warning modal shown before creating a work order */}
      <Modal
        opened={stockWarning !== null}
        onClose={() => setStockWarning(null)}
        title="Low stock on linked materials"
        size="md"
      >
        {stockWarning && (
          <>
            <Alert
              icon={<IconAlertTriangle size={16} />}
              color="orange"
              mb="md"
              data-testid="low-stock-banner"
            >
              Some materials for &ldquo;{stockWarning.item.title}&rdquo; are below minimum stock.
            </Alert>
            <List size="sm" spacing="xs" mb="md">
              {stockWarning.alerts.map((alert) => (
                <List.Item key={alert.material_id} data-testid="low-stock-alert">
                  Low stock: {alert.name}: {alert.current}/{alert.minimum}
                </List.Item>
              ))}
            </List>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setStockWarning(null)}>
                Cancel
              </Button>
              <Button variant="subtle" color="gray" onClick={handleGenerateAnyway}>
                Generate without reordering
              </Button>
              <Button color="orange" loading={reordering} onClick={handleReorderAndGenerate}>
                Create reorder requests &amp; continue
              </Button>
            </Group>
          </>
        )}
      </Modal>

      {/* Manual PDF upload result modal */}
      <Modal
        opened={uploadResultOpened}
        onClose={closeUploadResult}
        title="Work Order PDF Upload"
        size="md"
      >
        {uploadResult && (
          <Stack gap="sm" data-testid="upload-result-modal">
            {uploadResult.status === 'applied' ? (
              <Alert icon={<IconCheck size={16} />} color="green">
                PDF applied successfully.
              </Alert>
            ) : (
              <Alert icon={<IconAlertTriangle size={16} />} color="red">
                Could not apply PDF.
              </Alert>
            )}
            {uploadResult.work_order_id && (
              <Text size="sm">
                Work Order:{' '}
                <Link to={`/maintenance/work-orders/${uploadResult.work_order_id}`}>
                  Open work order
                </Link>
              </Text>
            )}
            {uploadResult.completed_items.length > 0 && (
              <Box>
                <Text size="sm" fw={600} mb={4}>
                  Completed steps ({uploadResult.completed_items.length})
                </Text>
                <List size="sm" spacing={2}>
                  {uploadResult.completed_items.map((item) => (
                    <List.Item key={item.id} data-testid="upload-completed-item">
                      {item.task_title}
                    </List.Item>
                  ))}
                </List>
              </Box>
            )}
            {uploadResult.errors.length > 0 && (
              <Box>
                <Text size="sm" fw={600} c="red" mb={4}>
                  Errors
                </Text>
                <List size="sm" spacing={2}>
                  {uploadResult.errors.map((err, idx) => (
                    <List.Item key={idx} data-testid="upload-error-item">
                      {err}
                    </List.Item>
                  ))}
                </List>
              </Box>
            )}
            <Group justify="flex-end" mt="sm">
              <Button onClick={closeUploadResult}>Close</Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Container>
  );
};

export default MaintenanceDashboard;
