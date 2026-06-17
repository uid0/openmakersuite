/**
 * CommandPalette Component Tests
 *
 * Closes the biggest single AC-20 (a11y) gap for the #457 resilience program:
 * open-focus, keyboard navigation (Arrow/Enter/Escape), query-filtered results,
 * the searchbox role, and a clean axe audit.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { CommandPalette } from '../../components/CommandPalette';
import {
  getRecentSearches,
  saveRecentSearch,
  searchGlobal,
} from '../../services/searchService';
import { SearchResult } from '../../types';
import { expectNoA11yViolations } from '../helpers/axe';

vi.mock('../../services/searchService', () => ({
  // Pass-through debounce so a typed query triggers search synchronously.
  debounce: (fn: (...args: unknown[]) => unknown) => fn,
  searchGlobal: vi.fn(),
  getRecentSearches: vi.fn(),
  saveRecentSearch: vi.fn(),
}));

const mockedSearchGlobal = vi.mocked(searchGlobal);
const mockedGetRecent = vi.mocked(getRecentSearches);
const mockedSaveRecent = vi.mocked(saveRecentSearch);

const makeResult = (overrides: Partial<SearchResult> = {}): SearchResult => ({
  id: '1',
  type: 'inventory',
  title: 'Widget',
  subtitle: 'SKU-1',
  url: '/inventory/items/1',
  ...overrides,
});

const renderPalette = () => {
  const onClose = vi.fn();
  const utils = render(
    <MantineProvider>
      <MemoryRouter>
        <CommandPalette isOpen onClose={onClose} />
      </MemoryRouter>
    </MantineProvider>
  );
  return { onClose, ...utils };
};

describe('CommandPalette', () => {
  beforeEach(() => {
    mockedSearchGlobal.mockResolvedValue([]);
    mockedGetRecent.mockResolvedValue([]);
    mockedSaveRecent.mockResolvedValue(undefined);
  });

  it('exposes the input as a named searchbox and focuses it when opened', async () => {
    renderPalette();
    const searchbox = await screen.findByRole('searchbox', { name: /search/i });
    await waitFor(() => expect(searchbox).toHaveFocus());
  });

  it('filters quick actions by the typed query', async () => {
    renderPalette();
    const searchbox = await screen.findByRole('searchbox');

    fireEvent.change(searchbox, { target: { value: 'asset' } });

    expect(await screen.findByText('Go to Assets')).toBeInTheDocument();
    expect(screen.getByText('Create Asset')).toBeInTheDocument();
    // An unrelated action must not leak into the filtered results.
    expect(screen.queryByText('Go to Locations')).not.toBeInTheDocument();
  });

  it('closes when Escape is pressed', async () => {
    const { onClose } = renderPalette();
    const searchbox = await screen.findByRole('searchbox');

    fireEvent.keyDown(searchbox, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('moves the selection with the down arrow key', async () => {
    mockedSearchGlobal.mockResolvedValue([
      makeResult({ id: 'a', title: 'Result A', url: '/a' }),
      makeResult({ id: 'b', title: 'Result B', url: '/b' }),
    ]);
    // Modal content portals to document.body, so query baseElement, not container.
    const { baseElement } = renderPalette();
    const searchbox = await screen.findByRole('searchbox');

    // 'zzz' matches no quick actions, so only the two search results render.
    fireEvent.change(searchbox, { target: { value: 'zzz' } });
    await screen.findByText('Result A');

    // First result is selected on load.
    await waitFor(() =>
      expect(baseElement.querySelector('.command-palette-item.selected')).toHaveTextContent(
        'Result A'
      )
    );

    fireEvent.keyDown(searchbox, { key: 'ArrowDown' });
    await waitFor(() =>
      expect(baseElement.querySelector('.command-palette-item.selected')).toHaveTextContent(
        'Result B'
      )
    );
  });

  it('selects the highlighted result on Enter', async () => {
    mockedSearchGlobal.mockResolvedValue([
      makeResult({ id: 'a', title: 'Result A', url: '/a' }),
    ]);
    const { onClose } = renderPalette();
    const searchbox = await screen.findByRole('searchbox');

    fireEvent.change(searchbox, { target: { value: 'zzz' } });
    await screen.findByText('Result A');

    fireEvent.keyDown(searchbox, { key: 'Enter' });

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockedSaveRecent).toHaveBeenCalled();
  });

  it('has no accessibility violations when open', async () => {
    const { baseElement } = renderPalette();
    await screen.findByRole('searchbox');
    await expectNoA11yViolations(baseElement);
  });
});
