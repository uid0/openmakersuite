/**
 * Screen edit / customization page.
 *
 * Committee/SIG leaders edit a screen's metadata and content blocks here.
 * Important system messages are managed elsewhere and cannot be disabled
 * from this page.
 */
import {
  ActionIcon,
  Badge,
  Button,
  Divider,
  Group,
  Loader,
  NumberInput,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { IconTrash } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { screensAPI } from '../services/api';
import { Screen, ScreenBlockType, ScreenContentBlock } from '../types';

const BLOCK_TYPE_OPTIONS: { value: ScreenBlockType; label: string }[] = [
  { value: 'tool_status', label: 'Tool Status' },
  { value: 'asset_usage', label: 'Asset Usage' },
  { value: 'custom_text', label: 'Custom Text / Rules Reminder' },
  { value: 'announcement', label: 'Announcement / Class Slide' },
  { value: 'weather', label: 'Weather' },
  { value: 'shared_weather', label: 'Shared Weather' },
  { value: 'traffic', label: 'Traffic' },
  { value: 'member_count', label: 'Member Count' },
  { value: 'board_meeting', label: 'Next Board Meeting' },
];

const ScreenEditPage: React.FC = () => {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const [screen, setScreen] = useState<Screen | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [newBlockType, setNewBlockType] = useState<ScreenBlockType>('custom_text');

  const load = useCallback(async () => {
    if (!slug) return;
    try {
      setLoading(true);
      setError(null);
      const resp = await screensAPI.getScreen(slug);
      setScreen(resp.data);
    } catch (err) {
      console.error('Failed to load screen', err);
      setError('Unable to load screen.');
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSaveMetadata = async () => {
    if (!screen) return;
    try {
      setSaving(true);
      const resp = await screensAPI.updateScreen(screen.slug, {
        name: screen.name,
        description: screen.description,
        is_active: screen.is_active,
        rotation_interval_seconds: screen.rotation_interval_seconds,
        refresh_interval_seconds: screen.refresh_interval_seconds,
      });
      setScreen(resp.data);
    } catch (err) {
      console.error('Save failed', err);
      window.alert('Unable to save. Check permissions.');
    } finally {
      setSaving(false);
    }
  };

  const handleAddBlock = async () => {
    if (!screen) return;
    try {
      const resp = await screensAPI.createBlock({
        screen: screen.id,
        block_type: newBlockType,
        title: '',
        body: '',
        order: screen.content_blocks.length,
        is_enabled: true,
      });
      setScreen({
        ...screen,
        content_blocks: [...screen.content_blocks, resp.data],
      });
    } catch (err) {
      console.error('Add block failed', err);
      window.alert('Unable to add block.');
    }
  };

  const handleBlockChange = (blockId: string, updates: Partial<ScreenContentBlock>) => {
    if (!screen) return;
    setScreen({
      ...screen,
      content_blocks: screen.content_blocks.map((b) =>
        b.id === blockId ? { ...b, ...updates } : b,
      ),
    });
  };

  const handleBlockSave = async (block: ScreenContentBlock) => {
    try {
      await screensAPI.updateBlock(block.id, {
        title: block.title,
        body: block.body,
        order: block.order,
        is_enabled: block.is_enabled,
      });
    } catch (err) {
      console.error('Block save failed', err);
      window.alert('Unable to save block.');
    }
  };

  const handleBlockDelete = async (blockId: string) => {
    if (!screen) return;
    if (!window.confirm('Delete this content block?')) return;
    try {
      await screensAPI.deleteBlock(blockId);
      setScreen({
        ...screen,
        content_blocks: screen.content_blocks.filter((b) => b.id !== blockId),
      });
    } catch (err) {
      console.error('Delete failed', err);
      window.alert('Unable to delete block.');
    }
  };

  const handleRotateToken = async () => {
    if (!screen) return;
    if (!window.confirm('Rotate the kiosk access token? Any device already using the old token will stop working.')) {
      return;
    }
    try {
      const resp = await screensAPI.rotateToken(screen.slug);
      setScreen({ ...screen, access_token: resp.data.access_token });
    } catch (err) {
      console.error('Rotate failed', err);
      window.alert('Unable to rotate token.');
    }
  };

  if (loading) {
    return (
      <Paper p="md">
        <Group><Loader size="sm" /><Text>Loading screen…</Text></Group>
      </Paper>
    );
  }

  if (error || !screen) {
    return (
      <Paper p="md">
        <Text c="red">{error || 'Screen not found.'}</Text>
        <Button variant="default" onClick={() => navigate('/facilities/screens')} mt="md">
          Back to Screens
        </Button>
      </Paper>
    );
  }

  const kioskUrl = `${window.location.origin}/kiosk/${screen.slug}?token=${screen.access_token}`;

  return (
    <Paper p="md" shadow="xs">
      <Group justify="space-between" mb="md">
        <Title order={2}>Screen: {screen.name}</Title>
        <Badge color={screen.is_online ? 'teal' : 'red'} variant="light">
          {screen.is_online ? 'Online' : 'Offline'}
        </Badge>
      </Group>

      <Stack gap="md">
        <TextInput
          label="Name"
          value={screen.name}
          onChange={(e) => setScreen({ ...screen, name: e.currentTarget.value })}
        />
        <Textarea
          label="Description"
          value={screen.description}
          onChange={(e) => setScreen({ ...screen, description: e.currentTarget.value })}
          autosize
          minRows={2}
        />
        <Group grow>
          <NumberInput
            label="Rotation interval (seconds)"
            value={screen.rotation_interval_seconds}
            onChange={(v) => setScreen({ ...screen, rotation_interval_seconds: Number(v) || 15 })}
            min={5}
            max={600}
          />
          <NumberInput
            label="Refresh interval (seconds)"
            value={screen.refresh_interval_seconds}
            onChange={(v) => setScreen({ ...screen, refresh_interval_seconds: Number(v) || 60 })}
            min={15}
            max={3600}
          />
        </Group>
        <Switch
          label="Active"
          checked={screen.is_active}
          onChange={(e) => setScreen({ ...screen, is_active: e.currentTarget.checked })}
        />
        <Group>
          <Button onClick={handleSaveMetadata} loading={saving}>Save</Button>
          <Button variant="default" onClick={() => navigate('/facilities/screens')}>Cancel</Button>
          <Button variant="outline" color="orange" onClick={handleRotateToken}>
            Rotate Access Token
          </Button>
        </Group>

        <Paper p="sm" withBorder>
          <Text size="sm" c="dimmed">Kiosk URL (point your Chromecast / kiosk browser here):</Text>
          <Text style={{ wordBreak: 'break-all' }} data-testid="kiosk-url">{kioskUrl}</Text>
        </Paper>

        <Divider label="Content Blocks" labelPosition="left" my="sm" />

        <Group>
          <Select
            data={BLOCK_TYPE_OPTIONS}
            value={newBlockType}
            onChange={(v) => v && setNewBlockType(v as ScreenBlockType)}
          />
          <Button onClick={handleAddBlock}>Add Block</Button>
        </Group>

        {screen.content_blocks.length === 0 ? (
          <Text c="dimmed">
            This screen has no content blocks yet. Add one to display something besides
            system-wide messages.
          </Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Order</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Title</Table.Th>
                <Table.Th>Body</Table.Th>
                <Table.Th>Enabled</Table.Th>
                <Table.Th>Actions</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {screen.content_blocks
                .slice()
                .sort((a, b) => a.order - b.order)
                .map((block) => (
                  <Table.Tr key={block.id} data-testid={`block-row-${block.id}`}>
                    <Table.Td>
                      <NumberInput
                        value={block.order}
                        onChange={(v) =>
                          handleBlockChange(block.id, { order: Number(v) || 0 })
                        }
                        min={0}
                        w={80}
                      />
                    </Table.Td>
                    <Table.Td>{block.block_type_display || block.block_type}</Table.Td>
                    <Table.Td>
                      <TextInput
                        value={block.title}
                        onChange={(e) =>
                          handleBlockChange(block.id, { title: e.currentTarget.value })
                        }
                      />
                    </Table.Td>
                    <Table.Td>
                      <Textarea
                        value={block.body}
                        onChange={(e) =>
                          handleBlockChange(block.id, { body: e.currentTarget.value })
                        }
                        autosize
                        minRows={1}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Switch
                        checked={block.is_enabled}
                        onChange={(e) =>
                          handleBlockChange(block.id, { is_enabled: e.currentTarget.checked })
                        }
                      />
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs">
                        <Button size="xs" onClick={() => handleBlockSave(block)}>
                          Save
                        </Button>
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          aria-label="Delete block"
                          onClick={() => handleBlockDelete(block.id)}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Paper>
  );
};

export default ScreenEditPage;
