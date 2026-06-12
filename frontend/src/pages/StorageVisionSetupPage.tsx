/**
 * Storage Vision setup screen (AC-28 setup half + AC-29 non-staff gate).
 *
 * One staff/Logistics-only page that covers area, slot, and camera
 * management plus the marker label download — the configuration
 * surface a Facilities operator needs before the phone-capture (slice
 * 8) and review-queue (slice 9) screens are useful.
 *
 * AC-29: non-staff/non-Logistics users get bounced; the same gate
 * also hides the sidebar link (App.tsx + Sidebar).
 */
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Code,
  Group,
  LoadingOverlay,
  Modal,
  NumberInput,
  Select,
  Stack,
  Switch,
  Table,
  Tabs,
  Text,
  TextInput,
  Textarea,
  Title,
  Tooltip,
} from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  inventoryAPI,
  storageVisionAPI,
  VisionArea,
  VisionAreaInput,
  VisionCamera,
  VisionCameraInput,
  VisionCameraWithToken,
  VisionSlot,
  VisionSlotInput,
} from '../services/api';
import { Category, InventoryItem, Location } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type Tab = 'areas' | 'slots' | 'cameras';

const unwrap = <T,>(data: { results: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : data.results;

const StorageVisionSetupPage: React.FC = () => {
  const isStaff =
    typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' &&
    localStorage.getItem('is_superuser') === 'true';
  const isLogistics =
    typeof window !== 'undefined' &&
    (localStorage.getItem('is_logistics') === 'true' ||
      (localStorage.getItem('groups') || '').includes('Logistics'));
  const isAllowed = isStaff || isSuperuser || isLogistics;

  const [tab, setTab] = useState<Tab>('areas');

  const [areas, setAreas] = useState<VisionArea[]>([]);
  const [slots, setSlots] = useState<VisionSlot[]>([]);
  const [cameras, setCameras] = useState<VisionCamera[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [areaModal, setAreaModal] = useState<{
    open: boolean;
    editing: VisionArea | null;
  }>({ open: false, editing: null });
  const [slotModal, setSlotModal] = useState<{
    open: boolean;
    editing: VisionSlot | null;
  }>({ open: false, editing: null });
  const [cameraModal, setCameraModal] = useState<{
    open: boolean;
    editing: VisionCamera | null;
  }>({ open: false, editing: null });

  // AC-7: the raw bearer is shown ONCE and then never again. The
  // server returns it on create/rotate; the page stashes it locally
  // and only renders it until the modal is dismissed.
  const [revealedToken, setRevealedToken] =
    useState<VisionCameraWithToken | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [areasRes, slotsRes, camerasRes, locRes, itemsRes, catRes] =
        await Promise.all([
          storageVisionAPI.listAreas(),
          storageVisionAPI.listSlots(),
          storageVisionAPI.listCameras(),
          inventoryAPI.listLocations(),
          inventoryAPI.listItems({
            is_active: true,
            ordering: 'name',
            page_size: 500,
          }),
          inventoryAPI.listCategories(),
        ]);
      setAreas(unwrap(areasRes.data));
      setSlots(unwrap(slotsRes.data));
      setCameras(unwrap(camerasRes.data));
      setLocations(
        Array.isArray(locRes.data)
          ? (locRes.data as Location[])
          : ((locRes.data as { results?: Location[] }).results ?? []),
      );
      setItems(itemsRes.data.results);
      // categories are used to filter the item picker label
      void catRes;
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load storage vision setup.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAllowed) return;
    load();
  }, [isAllowed, load]);

  if (!isAllowed) {
    return <Navigate to="/" replace />;
  }

  // ----- area CRUD -----
  const submitArea = async (form: VisionAreaInput, editingId: number | null) => {
    try {
      if (editingId == null) {
        const res = await storageVisionAPI.createArea(form);
        setAreas((prev) => [...prev, res.data]);
      } else {
        const res = await storageVisionAPI.updateArea(editingId, form);
        setAreas((prev) => prev.map((a) => (a.id === editingId ? res.data : a)));
      }
      setAreaModal({ open: false, editing: null });
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to save the area.'));
    }
  };

  const deleteArea = async (id: number) => {
    if (!window.confirm('Delete this area?')) return;
    try {
      await storageVisionAPI.deleteArea(id);
      setAreas((prev) => prev.filter((a) => a.id !== id));
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to delete the area.'));
    }
  };

  // ----- slot CRUD -----
  const submitSlot = async (form: VisionSlotInput, editingId: number | null) => {
    try {
      if (editingId == null) {
        const res = await storageVisionAPI.createSlot(form);
        setSlots((prev) => [...prev, res.data]);
      } else {
        const res = await storageVisionAPI.updateSlot(editingId, form);
        setSlots((prev) => prev.map((s) => (s.id === editingId ? res.data : s)));
      }
      setSlotModal({ open: false, editing: null });
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to save the slot.'));
    }
  };

  const deleteSlot = async (id: number) => {
    if (!window.confirm('Delete this slot?')) return;
    try {
      await storageVisionAPI.deleteSlot(id);
      setSlots((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to delete the slot.'));
    }
  };

  const downloadMarker = async (slot: VisionSlot) => {
    try {
      const res = await storageVisionAPI.downloadSlotMarker(slot.id);
      const blob = new Blob([res.data], { type: 'image/png' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `vision-slot-${slot.marker_code}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to fetch the marker label.'));
    }
  };

  // ----- camera CRUD -----
  const submitCamera = async (
    form: VisionCameraInput,
    editingId: number | null,
  ) => {
    try {
      if (editingId == null) {
        const res = await storageVisionAPI.createCamera(form);
        // AC-7: surface the raw token to the operator EXACTLY once.
        setRevealedToken(res.data);
        setCameras((prev) => [...prev, res.data]);
      } else {
        const res = await storageVisionAPI.updateCamera(editingId, form);
        setCameras((prev) =>
          prev.map((c) => (c.id === editingId ? res.data : c)),
        );
      }
      setCameraModal({ open: false, editing: null });
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to save the camera.'));
    }
  };

  const deleteCamera = async (id: number) => {
    if (!window.confirm('Delete this camera?')) return;
    try {
      await storageVisionAPI.deleteCamera(id);
      setCameras((prev) => prev.filter((c) => c.id !== id));
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to delete the camera.'));
    }
  };

  const rotateToken = async (camera: VisionCamera) => {
    if (
      !window.confirm(
        `Rotate token for ${camera.name}? The old token stops working immediately.`,
      )
    ) {
      return;
    }
    try {
      const res = await storageVisionAPI.rotateCameraToken(camera.id);
      setRevealedToken(res.data);
      setCameras((prev) =>
        prev.map((c) => (c.id === camera.id ? res.data : c)),
      );
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to rotate the token.'));
    }
  };

  return (
    <WorkspacePage
      hero={{
        title: 'Storage vision setup',
        description:
          'Manage monitored areas, marker-backed slots, and fixed cameras for the supply-reorder workflow.',
        eyebrow: 'Facilities',
        action: (
          <Button
            component={Link}
            to="/facilities/storage-vision/capture"
            data-testid="setup-page-upload-cta"
          >
            Upload capture
          </Button>
        ),
      }}
      testId="storage-vision-setup-page"
    >
      <Box pos="relative">
        <LoadingOverlay visible={loading} />
        {error && (
          <Alert color="red" mb="md" onClose={() => setError(null)} withCloseButton>
            {error}
          </Alert>
        )}

        <Tabs value={tab} onChange={(v) => setTab((v as Tab) || 'areas')}>
          <Tabs.List>
            <Tabs.Tab value="areas">Areas ({areas.length})</Tabs.Tab>
            <Tabs.Tab value="slots">Slots ({slots.length})</Tabs.Tab>
            <Tabs.Tab value="cameras">Cameras ({cameras.length})</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="areas" pt="md">
            <AreasPanel
              areas={areas}
              locations={locations}
              onNew={() => setAreaModal({ open: true, editing: null })}
              onEdit={(a) => setAreaModal({ open: true, editing: a })}
              onDelete={deleteArea}
            />
          </Tabs.Panel>

          <Tabs.Panel value="slots" pt="md">
            <SlotsPanel
              slots={slots}
              areas={areas}
              items={items}
              onNew={() => setSlotModal({ open: true, editing: null })}
              onEdit={(s) => setSlotModal({ open: true, editing: s })}
              onDelete={deleteSlot}
              onDownloadMarker={downloadMarker}
            />
          </Tabs.Panel>

          <Tabs.Panel value="cameras" pt="md">
            <CamerasPanel
              cameras={cameras}
              areas={areas}
              onNew={() => setCameraModal({ open: true, editing: null })}
              onEdit={(c) => setCameraModal({ open: true, editing: c })}
              onDelete={deleteCamera}
              onRotateToken={rotateToken}
            />
          </Tabs.Panel>
        </Tabs>
      </Box>

      <AreaModal
        state={areaModal}
        locations={locations}
        onClose={() => setAreaModal({ open: false, editing: null })}
        onSubmit={submitArea}
      />
      <SlotModal
        state={slotModal}
        areas={areas}
        items={items}
        onClose={() => setSlotModal({ open: false, editing: null })}
        onSubmit={submitSlot}
      />
      <CameraModal
        state={cameraModal}
        areas={areas}
        onClose={() => setCameraModal({ open: false, editing: null })}
        onSubmit={submitCamera}
      />
      <RevealTokenModal
        camera={revealedToken}
        onClose={() => setRevealedToken(null)}
      />
    </WorkspacePage>
  );
};

// ---------------------------------------------------------------------------
// Panel sub-components
// ---------------------------------------------------------------------------

interface AreasPanelProps {
  areas: VisionArea[];
  locations: Location[];
  onNew: () => void;
  onEdit: (a: VisionArea) => void;
  onDelete: (id: number) => void;
}

const AreasPanel: React.FC<AreasPanelProps> = ({
  areas,
  onNew,
  onEdit,
  onDelete,
}) => (
  <Stack>
    <Group justify="space-between">
      <Title order={4}>Monitored areas</Title>
      <Button onClick={onNew} data-testid="new-area-button">
        New area
      </Button>
    </Group>
    {areas.length === 0 ? (
      <Text c="dimmed">No areas configured yet.</Text>
    ) : (
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Location</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Actions</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {areas.map((a) => (
            <Table.Tr key={a.id}>
              <Table.Td>{a.name}</Table.Td>
              <Table.Td>{a.location_name}</Table.Td>
              <Table.Td>
                <Badge color={a.is_active ? 'green' : 'gray'}>
                  {a.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Group gap="xs">
                  <Button size="xs" variant="default" onClick={() => onEdit(a)}>
                    Edit
                  </Button>
                  <Button
                    size="xs"
                    color="red"
                    variant="light"
                    onClick={() => onDelete(a.id)}
                  >
                    Delete
                  </Button>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )}
  </Stack>
);

interface SlotsPanelProps {
  slots: VisionSlot[];
  areas: VisionArea[];
  items: InventoryItem[];
  onNew: () => void;
  onEdit: (s: VisionSlot) => void;
  onDelete: (id: number) => void;
  onDownloadMarker: (s: VisionSlot) => void;
}

const SlotsPanel: React.FC<SlotsPanelProps> = ({
  slots,
  onNew,
  onEdit,
  onDelete,
  onDownloadMarker,
}) => (
  <Stack>
    <Group justify="space-between">
      <Title order={4}>Marker-backed slots</Title>
      <Button onClick={onNew} data-testid="new-slot-button">
        New slot
      </Button>
    </Group>
    {slots.length === 0 ? (
      <Text c="dimmed">No slots configured yet.</Text>
    ) : (
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Marker</Table.Th>
            <Table.Th>Area</Table.Th>
            <Table.Th>Item</Table.Th>
            <Table.Th>Threshold</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Actions</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {slots.map((s) => (
            <Table.Tr key={s.id}>
              <Table.Td>
                <Code>{s.marker_code}</Code>
              </Table.Td>
              <Table.Td>{s.area_name}</Table.Td>
              <Table.Td>{s.item_name}</Table.Td>
              <Table.Td>{s.empty_low_confidence_threshold}</Table.Td>
              <Table.Td>
                <Badge color={s.is_active ? 'green' : 'gray'}>
                  {s.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Group gap="xs">
                  <Tooltip label="Download printable marker label">
                    <Button
                      size="xs"
                      variant="default"
                      onClick={() => onDownloadMarker(s)}
                      data-testid={`marker-download-${s.id}`}
                    >
                      Marker
                    </Button>
                  </Tooltip>
                  <Button size="xs" variant="default" onClick={() => onEdit(s)}>
                    Edit
                  </Button>
                  <Button
                    size="xs"
                    color="red"
                    variant="light"
                    onClick={() => onDelete(s.id)}
                  >
                    Delete
                  </Button>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )}
  </Stack>
);

interface CamerasPanelProps {
  cameras: VisionCamera[];
  areas: VisionArea[];
  onNew: () => void;
  onEdit: (c: VisionCamera) => void;
  onDelete: (id: number) => void;
  onRotateToken: (c: VisionCamera) => void;
}

const CamerasPanel: React.FC<CamerasPanelProps> = ({
  cameras,
  onNew,
  onEdit,
  onDelete,
  onRotateToken,
}) => (
  <Stack>
    <Group justify="space-between">
      <Title order={4}>Fixed cameras</Title>
      <Button onClick={onNew} data-testid="new-camera-button">
        New camera
      </Button>
    </Group>
    {cameras.length === 0 ? (
      <Text c="dimmed">No cameras provisioned yet.</Text>
    ) : (
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Area</Table.Th>
            <Table.Th>Token fingerprint</Table.Th>
            <Table.Th>Last seen</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Actions</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {cameras.map((c) => (
            <Table.Tr key={c.id}>
              <Table.Td>{c.name}</Table.Td>
              <Table.Td>{c.area_name ?? '—'}</Table.Td>
              <Table.Td>
                <Code>{c.token_fingerprint}</Code>
              </Table.Td>
              <Table.Td>
                {c.last_seen_at
                  ? new Date(c.last_seen_at).toLocaleString()
                  : 'never'}
              </Table.Td>
              <Table.Td>
                <Badge color={c.is_active ? 'green' : 'gray'}>
                  {c.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Group gap="xs">
                  <Button
                    size="xs"
                    variant="default"
                    onClick={() => onRotateToken(c)}
                  >
                    Rotate token
                  </Button>
                  <Button size="xs" variant="default" onClick={() => onEdit(c)}>
                    Edit
                  </Button>
                  <Button
                    size="xs"
                    color="red"
                    variant="light"
                    onClick={() => onDelete(c.id)}
                  >
                    Delete
                  </Button>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )}
  </Stack>
);

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------

interface AreaModalProps {
  state: { open: boolean; editing: VisionArea | null };
  locations: Location[];
  onClose: () => void;
  onSubmit: (form: VisionAreaInput, editingId: number | null) => void;
}

const AreaModal: React.FC<AreaModalProps> = ({
  state,
  locations,
  onClose,
  onSubmit,
}) => {
  const [name, setName] = useState('');
  const [location, setLocation] = useState<number | null>(null);
  const [description, setDescription] = useState('');
  const [isActive, setIsActive] = useState(true);

  useEffect(() => {
    if (state.open && state.editing) {
      setName(state.editing.name);
      setLocation(state.editing.location);
      setDescription(state.editing.description);
      setIsActive(state.editing.is_active);
    } else if (state.open) {
      setName('');
      setLocation(null);
      setDescription('');
      setIsActive(true);
    }
  }, [state.open, state.editing]);

  const locationOptions = useMemo(
    () => locations.map((l) => ({ value: String(l.id), label: l.name })),
    [locations],
  );

  return (
    <Modal
      opened={state.open}
      onClose={onClose}
      title={state.editing ? 'Edit area' : 'New area'}
      data-testid="area-modal"
    >
      <Stack>
        <TextInput
          label="Name"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          required
        />
        <Select
          label="Location"
          data={locationOptions}
          value={location != null ? String(location) : null}
          onChange={(v) => setLocation(v ? Number(v) : null)}
          required
          searchable
        />
        <Textarea
          label="Description"
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <Switch
          label="Active"
          checked={isActive}
          onChange={(e) => setIsActive(e.currentTarget.checked)}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!name || location == null}
            onClick={() =>
              onSubmit(
                {
                  name,
                  location: location!,
                  description,
                  is_active: isActive,
                },
                state.editing?.id ?? null,
              )
            }
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};

interface SlotModalProps {
  state: { open: boolean; editing: VisionSlot | null };
  areas: VisionArea[];
  items: InventoryItem[];
  onClose: () => void;
  onSubmit: (form: VisionSlotInput, editingId: number | null) => void;
}

const SlotModal: React.FC<SlotModalProps> = ({
  state,
  areas,
  items,
  onClose,
  onSubmit,
}) => {
  const [area, setArea] = useState<number | null>(null);
  const [item, setItem] = useState<string | null>(null);
  const [markerCode, setMarkerCode] = useState('');
  const [threshold, setThreshold] = useState<number>(0.5);
  const [notes, setNotes] = useState('');
  const [isActive, setIsActive] = useState(true);

  useEffect(() => {
    if (state.open && state.editing) {
      setArea(state.editing.area);
      setItem(state.editing.item);
      setMarkerCode(state.editing.marker_code);
      setThreshold(Number(state.editing.empty_low_confidence_threshold));
      setNotes(state.editing.notes);
      setIsActive(state.editing.is_active);
    } else if (state.open) {
      setArea(null);
      setItem(null);
      setMarkerCode('');
      setThreshold(0.5);
      setNotes('');
      setIsActive(true);
    }
  }, [state.open, state.editing]);

  const areaOptions = useMemo(
    () => areas.map((a) => ({ value: String(a.id), label: a.name })),
    [areas],
  );
  const itemOptions = useMemo(
    () => items.map((i) => ({ value: i.id, label: i.name })),
    [items],
  );

  return (
    <Modal
      opened={state.open}
      onClose={onClose}
      title={state.editing ? 'Edit slot' : 'New slot'}
      data-testid="slot-modal"
    >
      <Stack>
        <Select
          label="Area"
          data={areaOptions}
          value={area != null ? String(area) : null}
          onChange={(v) => setArea(v ? Number(v) : null)}
          required
          searchable
        />
        <Select
          label="Item"
          data={itemOptions}
          value={item}
          onChange={setItem}
          required
          searchable
        />
        <TextInput
          label="Marker code"
          value={markerCode}
          onChange={(e) => setMarkerCode(e.currentTarget.value)}
          required
          description="Printed on the QR; e.g. VIS-BAY1-M3HEX"
        />
        <NumberInput
          label="Empty/low confidence threshold"
          value={threshold}
          onChange={(v) => setThreshold(Number(v) || 0)}
          min={0}
          max={1}
          step={0.05}
          decimalScale={2}
        />
        <Textarea
          label="Notes"
          value={notes}
          onChange={(e) => setNotes(e.currentTarget.value)}
        />
        <Switch
          label="Active"
          checked={isActive}
          onChange={(e) => setIsActive(e.currentTarget.checked)}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!area || !item || !markerCode}
            onClick={() =>
              onSubmit(
                {
                  area: area!,
                  item: item!,
                  marker_code: markerCode,
                  empty_low_confidence_threshold: threshold.toFixed(2),
                  notes,
                  is_active: isActive,
                },
                state.editing?.id ?? null,
              )
            }
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};

interface CameraModalProps {
  state: { open: boolean; editing: VisionCamera | null };
  areas: VisionArea[];
  onClose: () => void;
  onSubmit: (form: VisionCameraInput, editingId: number | null) => void;
}

const CameraModal: React.FC<CameraModalProps> = ({
  state,
  areas,
  onClose,
  onSubmit,
}) => {
  const [name, setName] = useState('');
  const [area, setArea] = useState<number | null>(null);
  const [isActive, setIsActive] = useState(true);

  useEffect(() => {
    if (state.open && state.editing) {
      setName(state.editing.name);
      setArea(state.editing.area);
      setIsActive(state.editing.is_active);
    } else if (state.open) {
      setName('');
      setArea(null);
      setIsActive(true);
    }
  }, [state.open, state.editing]);

  const areaOptions = useMemo(
    () => areas.map((a) => ({ value: String(a.id), label: a.name })),
    [areas],
  );

  return (
    <Modal
      opened={state.open}
      onClose={onClose}
      title={state.editing ? 'Edit camera' : 'New camera'}
      data-testid="camera-modal"
    >
      <Stack>
        <TextInput
          label="Name"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          required
        />
        <Select
          label="Bound area"
          data={areaOptions}
          value={area != null ? String(area) : null}
          onChange={(v) => setArea(v ? Number(v) : null)}
          searchable
          clearable
          description="Optional — a bound camera doesn't need to send area on every upload."
        />
        <Switch
          label="Active"
          checked={isActive}
          onChange={(e) => setIsActive(e.currentTarget.checked)}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!name}
            onClick={() =>
              onSubmit(
                { name, area: area ?? null, is_active: isActive },
                state.editing?.id ?? null,
              )
            }
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};

interface RevealTokenModalProps {
  camera: VisionCameraWithToken | null;
  onClose: () => void;
}

const RevealTokenModal: React.FC<RevealTokenModalProps> = ({
  camera,
  onClose,
}) => (
  <Modal
    opened={camera != null}
    onClose={onClose}
    title="Camera token (shown ONCE)"
    data-testid="reveal-token-modal"
    centered
  >
    {camera && (
      <Stack>
        <Alert color="yellow">
          This is the only time {camera.name}&apos;s token will be displayed.
          Copy it now and program the device — list and detail views will only
          show the fingerprint going forward.
        </Alert>
        <Card withBorder>
          <Stack gap="xs">
            <Text fw={600}>Bearer token</Text>
            <Code block data-testid="revealed-token">
              {camera.raw_token}
            </Code>
            <Group>
              <Text size="sm">Fingerprint:</Text>
              <Code>{camera.token_fingerprint}</Code>
            </Group>
          </Stack>
        </Card>
        <Group justify="flex-end">
          <Button
            variant="default"
            onClick={() => {
              if (navigator.clipboard) {
                void navigator.clipboard.writeText(camera.raw_token);
              }
            }}
          >
            Copy
          </Button>
          <Button onClick={onClose}>I&apos;ve copied it</Button>
        </Group>
      </Stack>
    )}
  </Modal>
);

export default StorageVisionSetupPage;
