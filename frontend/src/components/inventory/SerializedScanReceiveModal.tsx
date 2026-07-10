/**
 * SerializedScanReceiveModal — scan-driven batch receive for a serialized
 * InventoryItem (op-n4pi). Web parity with the ScanTTY batch-scan surface.
 *
 * The item is fixed for the batch (the modal is launched from that item's
 * serialized-units panel). Staff set an optional batch `lot` + `expiration_date`
 * that apply to every scan, then fire serials one after another into a
 * keyboard-wedge-friendly input: it autofocuses, submits on Enter, and clears +
 * refocuses so a handheld scanner can rattle through a box of units untouched.
 *
 * Each scan calls the idempotent `scan_receive` endpoint (get-or-create-and-
 * receive), so a double-scanned serial is tolerated and surfaced as a
 * "Duplicate" rather than an error. A running log shows new-vs-duplicate with a
 * count, and "Undo last" disposes the most recent newly-received unit. Backend
 * errors surface inline via the standard extractErrorMessage path.
 */
import {
  Alert,
  Badge,
  Button,
  Divider,
  Group,
  Modal,
  ScrollArea,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { IconArrowBackUp } from '@tabler/icons-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';

import { serializedComponentsAPI } from '../../services/api';
import { extractErrorMessage } from '../../utils/extractErrorMessage';

interface ReceivedEntry {
  /** Unique per scan event (a serial can be scanned more than once). */
  key: string;
  componentId: string;
  serialNumber: string;
  /** True when this scan minted+received a new unit; false on a re-scan. */
  created: boolean;
  /** True once "Undo last" has disposed the underlying unit. */
  undone: boolean;
}

interface Props {
  opened: boolean;
  onClose: () => void;
  /** The serialized item every scan in this batch is received against. */
  item: { id: string; name: string };
  /**
   * Called once on close when the batch received anything, so the launching
   * panel can refresh its units list + available/on-hand header.
   */
  onReceived?: () => void;
}

const SerializedScanReceiveModal: React.FC<Props> = ({
  opened,
  onClose,
  item,
  onReceived,
}) => {
  // Batch settings — applied to every scan in this session.
  const [lot, setLot] = useState('');
  const [expiration, setExpiration] = useState<string | null>(null);

  const [serial, setSerial] = useState('');
  const [received, setReceived] = useState<ReceivedEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [undoing, setUndoing] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const scanSeq = useRef(0);

  // Focus the scan field when the modal opens so a wedge scanner fires straight
  // in without a click.
  useEffect(() => {
    if (opened) inputRef.current?.focus();
  }, [opened]);

  const handleScan = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const sn = serial.trim();
      if (!sn) return;
      // Clear + refocus immediately so the next scan can land while this one is
      // still in flight (scanners fire faster than the round-trip).
      setSerial('');
      inputRef.current?.focus();
      setError(null);
      setBusy(true);
      const seq = (scanSeq.current += 1);
      try {
        const res = await serializedComponentsAPI.scanReceive({
          item: item.id,
          serial_number: sn,
          lot: lot.trim() || undefined,
          expiration_date: expiration || undefined,
        });
        const data = res?.data;
        if (data) {
          setReceived((prev) => [
            {
              key: `${seq}-${data.id}`,
              componentId: data.id,
              serialNumber: data.serial_number,
              created: data.created,
              undone: false,
            },
            ...prev,
          ]);
        }
      } catch (err) {
        setError(extractErrorMessage(err, `Could not receive ${sn}.`));
      } finally {
        setBusy(false);
      }
    },
    [serial, lot, expiration, item.id],
  );

  // Undo targets the most recent *newly-received* unit that has not already
  // been undone — a re-scan (duplicate) created nothing here, so undoing it
  // must never dispose pre-existing inventory.
  const lastUndoable = received.find((r) => r.created && !r.undone);

  const handleUndo = useCallback(async () => {
    if (!lastUndoable) return;
    setUndoing(true);
    setError(null);
    try {
      await serializedComponentsAPI.dispose(lastUndoable.componentId, {
        disposal_reason: 'Undo scan-receive (batch)',
      });
      setReceived((prev) =>
        prev.map((r) => (r.key === lastUndoable.key ? { ...r, undone: true } : r)),
      );
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not undo the last received unit.'));
    } finally {
      setUndoing(false);
      inputRef.current?.focus();
    }
  }, [lastUndoable]);

  const handleClose = useCallback(() => {
    // A batch that received (or undid) anything changed server state — let the
    // launching panel refresh once, on close, rather than per keystroke.
    if (received.length > 0) onReceived?.();
    setSerial('');
    setError(null);
    setReceived([]);
    setLot('');
    setExpiration(null);
    onClose();
  }, [received.length, onReceived, onClose]);

  const newCount = received.filter((r) => r.created && !r.undone).length;
  const dupCount = received.filter((r) => !r.created).length;

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title={`Receive serials — ${item.name}`}
      size="lg"
      centered
      data-testid="serialized-scan-receive-modal"
    >
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Set an optional lot / expiry for the batch, then scan or type each
          serial and press Enter. Serials are received against{' '}
          <Text span fw={500}>
            {item.name}
          </Text>
          . Re-scanning a serial is safe — it shows as a duplicate rather than an
          error.
        </Text>

        <Group grow align="flex-start">
          <TextInput
            label="Lot (optional)"
            placeholder="Batch / lot"
            value={lot}
            onChange={(e) => setLot(e.currentTarget.value)}
            data-testid="scan-receive-lot"
          />
          <DateInput
            label="Expires (optional)"
            placeholder="Batch expiry"
            value={expiration}
            onChange={setExpiration}
            clearable
            data-testid="scan-receive-expiration"
          />
        </Group>

        <form onSubmit={handleScan} data-testid="scan-receive-form">
          <TextInput
            ref={inputRef}
            label="Scan serial"
            placeholder="Scan or type a serial, then Enter"
            value={serial}
            onChange={(e) => setSerial(e.currentTarget.value)}
            data-autofocus
            data-testid="scan-receive-serial"
            rightSection={busy ? <Text size="xs" c="dimmed">…</Text> : undefined}
          />
        </form>

        {error && (
          <Alert
            color="red"
            withCloseButton
            onClose={() => setError(null)}
            data-testid="scan-receive-error"
          >
            {error}
          </Alert>
        )}

        <Divider />

        <Group justify="space-between" align="center">
          <Group gap="xs">
            <Badge color="green" variant="light" data-testid="scan-receive-new-count">
              {newCount} new
            </Badge>
            <Badge color="gray" variant="light" data-testid="scan-receive-dup-count">
              {dupCount} duplicate
            </Badge>
          </Group>
          <Button
            size="xs"
            variant="light"
            color="red"
            leftSection={<IconArrowBackUp size={14} />}
            onClick={handleUndo}
            disabled={!lastUndoable}
            loading={undoing}
            data-testid="scan-receive-undo"
          >
            Undo last
          </Button>
        </Group>

        {received.length === 0 ? (
          <Text c="dimmed" size="sm" data-testid="scan-receive-empty">
            No serials received yet.
          </Text>
        ) : (
          <ScrollArea.Autosize mah={260}>
            <Table verticalSpacing="xs" fz="sm" data-testid="scan-receive-log">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Serial</Table.Th>
                  <Table.Th>Result</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {received.map((entry) => (
                  <Table.Tr key={entry.key} data-testid="scan-receive-entry">
                    <Table.Td>
                      <Text
                        fw={500}
                        td={entry.undone ? 'line-through' : undefined}
                        c={entry.undone ? 'dimmed' : undefined}
                      >
                        {entry.serialNumber}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      {entry.undone ? (
                        <Badge color="red" variant="light">
                          Undone
                        </Badge>
                      ) : entry.created ? (
                        <Badge color="green" variant="light">
                          New
                        </Badge>
                      ) : (
                        <Badge color="gray" variant="light">
                          Duplicate
                        </Badge>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea.Autosize>
        )}

        <Group justify="flex-end">
          <Button onClick={handleClose} data-testid="scan-receive-done">
            Done
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};

export default SerializedScanReceiveModal;
