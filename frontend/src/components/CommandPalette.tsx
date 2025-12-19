/**
 * Command Palette Component
 * Global search and command palette accessible via Cmd/Ctrl+K
 */
import { Box, Divider, Group, Modal, ScrollArea, Stack, Text, TextInput } from '@mantine/core';
import { IconSearch, IconX } from '@tabler/icons-react';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useModalManager } from '../hooks/useModalManager';
import { debounce, getRecentSearches, saveRecentSearch, searchGlobal } from '../services/searchService';
import '../styles/CommandPalette.css';
import { RecentSearch, SearchResult } from '../types';
import { filterQuickActions, getQuickActions, QuickAction } from '../utils/commandPaletteActions';

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
}

type ResultItem = SearchResult | QuickAction | RecentSearch;

interface GroupedResults {
  type: 'search' | 'quick_action' | 'recent';
  label: string;
  items: ResultItem[];
}

const TYPE_LABELS: Record<string, string> = {
  inventory: 'Inventory Items',
  asset: 'Assets',
  purchase_order: 'Purchase Orders',
  supplier: 'Suppliers',
  location: 'Locations',
};

const TYPE_ICONS: Record<string, string> = {
  inventory: '📦',
  asset: '🔧',
  purchase_order: '📋',
  supplier: '🏪',
  location: '📍',
};

export const CommandPalette: React.FC<CommandPaletteProps> = ({ isOpen, onClose }) => {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [recentSearches, setRecentSearches] = useState<RecentSearch[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Register with modal manager (highest priority)
  useModalManager('command-palette', isOpen, onClose, 100);

  // Get quick actions
  const quickActions = useMemo(() => getQuickActions(navigate), [navigate]);
  const filteredQuickActions = useMemo(
    () => filterQuickActions(quickActions, query),
    [quickActions, query]
  );

  // Debounced search function
  const debouncedSearch = useMemo(
    () =>
      debounce(async (searchQuery: string) => {
        if (!searchQuery.trim()) {
          setSearchResults([]);
          setLoading(false);
          return;
        }

        setLoading(true);
        try {
          const results = await searchGlobal(searchQuery, 10);
          setSearchResults(results);
        } catch (error) {
          console.error('Search error:', error);
          setSearchResults([]);
        } finally {
          setLoading(false);
        }
      }, 300),
    []
  );

  // Load recent searches when modal opens
  useEffect(() => {
    if (isOpen) {
      loadRecentSearches();
      // Focus input when modal opens
      setTimeout(() => inputRef.current?.focus(), 100);
    } else {
      // Reset state when modal closes
      setQuery('');
      setSearchResults([]);
      setSelectedIndex(0);
    }
  }, [isOpen]);

  // Perform search when query changes
  useEffect(() => {
    if (query.trim()) {
      debouncedSearch(query);
    } else {
      setSearchResults([]);
      setLoading(false);
    }
  }, [query, debouncedSearch]);

  const loadRecentSearches = async () => {
    try {
      const recent = await getRecentSearches(10);
      setRecentSearches(recent);
    } catch (error) {
      console.error('Error loading recent searches:', error);
    }
  };

  // Group results by type
  const groupedResults = useMemo((): GroupedResults[] => {
    const groups: GroupedResults[] = [];

    // Quick actions (always shown when there's a query)
    if (query.trim() && filteredQuickActions.length > 0) {
      groups.push({
        type: 'quick_action',
        label: 'Quick Actions',
        items: filteredQuickActions,
      });
    }

    // Search results grouped by type
    if (searchResults.length > 0) {
      const byType: Record<string, SearchResult[]> = {};
      for (const result of searchResults) {
        if (!byType[result.type]) {
          byType[result.type] = [];
        }
        byType[result.type].push(result);
      }

      for (const [type, items] of Object.entries(byType)) {
        groups.push({
          type: 'search',
          label: TYPE_LABELS[type] || type,
          items,
        });
      }
    }

    // Recent searches (only shown when query is empty)
    if (!query.trim() && recentSearches.length > 0) {
      groups.push({
        type: 'recent',
        label: 'Recent Searches',
        items: recentSearches,
      });
    }

    return groups;
  }, [query, searchResults, filteredQuickActions, recentSearches]);

  // Calculate total items for keyboard navigation
  const totalItems = useMemo(() => {
    return groupedResults.reduce((sum, group) => sum + group.items.length, 0);
  }, [groupedResults]);

  // Reset selected index when results change
  useEffect(() => {
    setSelectedIndex(0);
  }, [groupedResults]);

  // Handle keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev) => Math.min(prev + 1, totalItems - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      handleSelectItem();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  const getUrlForRecentSearch = (recent: RecentSearch): string => {
    switch (recent.result_type) {
      case 'inventory':
        return `/inventory/items/${recent.result_id}`;
      case 'asset':
        return `/assets/${recent.result_id}`;
      case 'purchase_order':
        return `/purchasing/orders/${recent.result_id}`;
      case 'supplier':
        return `/inventory/suppliers/${recent.result_id}`;
      case 'location':
        return `/inventory/locations/${recent.result_id}`;
      default:
        return '/';
    }
  };

  const handleSelectItem = () => {
    let currentIndex = 0;
    for (const group of groupedResults) {
      for (const item of group.items) {
        if (currentIndex === selectedIndex) {
          handleItemClick(item, group.type);
          return;
        }
        currentIndex++;
      }
    }
  };

  const handleItemClick = async (item: ResultItem, groupType: string) => {
    if (groupType === 'quick_action' && 'action' in item) {
      // Quick action
      (item as QuickAction).action();
      onClose();
    } else if (groupType === 'search' && 'url' in item) {
      // Search result
      const result = item as SearchResult;
      await saveRecentSearch(query, result);
      navigate(result.url);
      onClose();
    } else if (groupType === 'recent' && 'result_id' in item) {
      // Recent search
      const recent = item as RecentSearch;
      navigate(getUrlForRecentSearch(recent));
      onClose();
    }
  };

  const renderItem = (item: ResultItem, groupType: string, index: number) => {
    const isSelected = index === selectedIndex;
    let title = '';
    let subtitle: string | null = null;
    let icon = '';

    if (groupType === 'quick_action' && 'label' in item) {
      const action = item as QuickAction;
      title = action.label;
      subtitle = action.description || null;
      icon = action.category === 'create' ? '➕' : '➡️';
    } else if (groupType === 'search' && 'title' in item) {
      const result = item as SearchResult;
      title = result.title;
      subtitle = result.subtitle || null;
      icon = TYPE_ICONS[result.type] || '📄';
    } else if (groupType === 'recent' && 'result_title' in item) {
      const recent = item as RecentSearch;
      title = recent.result_title;
      subtitle = `Searched: ${recent.query}`;
      icon = TYPE_ICONS[recent.result_type] || '🕐';
    }

    return (
      <Box
        key={`${groupType}-${index}`}
        className={`command-palette-item ${isSelected ? 'selected' : ''}`}
        onClick={() => handleItemClick(item, groupType)}
        onMouseEnter={() => setSelectedIndex(index)}
      >
        <Group gap="sm" p="xs">
          <Text size="lg">{icon}</Text>
          <Stack gap={2} style={{ flex: 1 }}>
            <Text size="sm" fw={500}>
              {title}
            </Text>
            {subtitle && (
              <Text size="xs" c="dimmed">
                {subtitle}
              </Text>
            )}
          </Stack>
        </Group>
      </Box>
    );
  };

  return (
    <Modal
      opened={isOpen}
      onClose={onClose}
      title={null}
      size="lg"
      padding={0}
      withCloseButton={false}
      overlayProps={{ opacity: 0.5, blur: 3 }}
      styles={{
        body: { padding: 0 },
        content: { maxHeight: '80vh' },
      }}
    >
      <Stack gap={0}>
        {/* Search Input */}
        <Box p="md">
          <TextInput
            ref={inputRef}
            placeholder="Search inventory, assets, POs, suppliers, locations..."
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            onKeyDown={handleKeyDown}
            leftSection={<IconSearch size={18} />}
            rightSection={
              query ? (
                <IconX
                  size={18}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setQuery('')}
                />
              ) : null
            }
            size="lg"
            autoFocus
          />
        </Box>

        <Divider />

        {/* Results */}
        <ScrollArea.Autosize mah={400}>
          <Box p="xs">
            {loading && (
              <Box p="md" style={{ textAlign: 'center' }}>
                <Text size="sm" c="dimmed">
                  Searching...
                </Text>
              </Box>
            )}

            {!loading && groupedResults.length === 0 && query.trim() && (
              <Box p="md" style={{ textAlign: 'center' }}>
                <Text size="sm" c="dimmed">
                  No results found
                </Text>
              </Box>
            )}

            {!loading && groupedResults.length === 0 && !query.trim() && (
              <Box p="md" style={{ textAlign: 'center' }}>
                <Text size="sm" c="dimmed">
                  Start typing to search, or use quick actions
                </Text>
              </Box>
            )}

            {groupedResults.map((group, groupIndex) => {
              let itemIndex = 0;
              // Calculate starting index for this group
              for (let i = 0; i < groupIndex; i++) {
                itemIndex += groupedResults[i].items.length;
              }

              return (
                <Box key={group.label} mb="xs">
                  <Text size="xs" fw={600} c="dimmed" px="xs" py="xs" style={{ textTransform: 'uppercase' }}>
                    {group.label}
                  </Text>
                  {group.items.map((item, itemIdx) => {
                    const globalIndex = itemIndex + itemIdx;
                    return renderItem(item, group.type, globalIndex);
                  })}
                </Box>
              );
            })}
          </Box>
        </ScrollArea.Autosize>

        {/* Footer hint */}
        <Divider />
        <Box p="xs" style={{ textAlign: 'center' }}>
          <Text size="xs" c="dimmed">
            <kbd>↑</kbd> <kbd>↓</kbd> Navigate • <kbd>Enter</kbd> Select • <kbd>Esc</kbd> Close
          </Text>
        </Box>
      </Stack>
    </Modal>
  );
};
