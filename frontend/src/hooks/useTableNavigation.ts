/**
 * Hook for keyboard navigation in tables
 * Handles arrow keys, Enter, Home, and End for row navigation
 */
import { useCallback, useEffect, useRef, useState } from 'react';

interface UseTableNavigationOptions<T> {
  rows: T[];
  onRowActivate?: (row: T, index: number) => void;
  enabled?: boolean;
  rowSelector?: string; // CSS selector for table rows, default: 'tr[data-row-index]'
}

/**
 * Hook for table keyboard navigation
 * @param rows - Array of row data
 * @param onRowActivate - Callback when a row is activated (Enter key)
 * @param enabled - Whether navigation is enabled (default: true)
 * @param rowSelector - CSS selector for table rows
 */
export function useTableNavigation<T>({
  rows,
  onRowActivate,
  enabled = true,
  rowSelector = 'tr[data-row-index]',
}: UseTableNavigationOptions<T>) {
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);
  const tableRef = useRef<HTMLElement | null>(null);

  // Scroll focused row into view
  const scrollToRow = useCallback((index: number) => {
    if (!tableRef.current) return;

    const rowElement = tableRef.current.querySelector(
      `${rowSelector}[data-row-index="${index}"]`
    ) as HTMLElement;

    if (rowElement) {
      rowElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [rowSelector]);

  // Handle keyboard navigation
  const handleKeyDown = useCallback((event: KeyboardEvent) => {
    if (!enabled || rows.length === 0) return;

    // Don't handle navigation when user is typing in an input
    const target = event.target as HTMLElement;
    const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable;
    if (isInput) return;

    let newIndex: number | null = null;

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        if (focusedIndex === null) {
          newIndex = 0;
        } else {
          newIndex = Math.min(focusedIndex + 1, rows.length - 1);
        }
        break;

      case 'ArrowUp':
        event.preventDefault();
        if (focusedIndex === null) {
          newIndex = rows.length - 1;
        } else {
          newIndex = Math.max(focusedIndex - 1, 0);
        }
        break;

      case 'Home':
        event.preventDefault();
        newIndex = 0;
        break;

      case 'End':
        event.preventDefault();
        newIndex = rows.length - 1;
        break;

      case 'Enter':
        if (focusedIndex !== null && focusedIndex >= 0 && focusedIndex < rows.length) {
          event.preventDefault();
          const row = rows[focusedIndex];
          onRowActivate?.(row, focusedIndex);
        }
        return; // Don't update focusedIndex for Enter

      case 'Escape':
        // Clear focus
        setFocusedIndex(null);
        return;

      default:
        return; // Don't handle other keys
    }

    if (newIndex !== null) {
      setFocusedIndex(newIndex);
      scrollToRow(newIndex);
    }
  }, [enabled, rows, focusedIndex, onRowActivate, scrollToRow]);

  useEffect(() => {
    if (enabled) {
      window.addEventListener('keydown', handleKeyDown);
      return () => {
        window.removeEventListener('keydown', handleKeyDown);
      };
    }
  }, [enabled, handleKeyDown]);

  // Set table ref
  const setTableRef = useCallback((element: HTMLElement | null) => {
    tableRef.current = element;
  }, []);

  // Get props for a table row
  const getRowProps = useCallback((index: number) => {
    return {
      'data-row-index': index,
      'data-focused': focusedIndex === index ? 'true' : undefined,
      tabIndex: focusedIndex === index ? 0 : -1,
      className: focusedIndex === index ? 'keyboard-focused-row' : undefined,
    };
  }, [focusedIndex]);

  // Clear focus
  const clearFocus = useCallback(() => {
    setFocusedIndex(null);
  }, []);

  return {
    focusedIndex,
    setTableRef,
    getRowProps,
    clearFocus,
  };
}
