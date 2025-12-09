/**
 * Search service for global search and recent searches
 */
import { RecentSearch, SearchResult } from '../types';
import { searchAPI } from './api';

const RECENT_SEARCHES_KEY = 'recent_searches';
const MAX_LOCAL_RECENT_SEARCHES = 10;

/**
 * Debounce utility function
 */
export function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null;
  return function executedFunction(...args: Parameters<T>) {
    const later = () => {
      timeout = null;
      func(...args);
    };
    if (timeout) {
      clearTimeout(timeout);
    }
    timeout = setTimeout(later, wait);
  };
}

/**
 * Get recent searches from localStorage
 */
export function getLocalRecentSearches(): RecentSearch[] {
  try {
    const stored = localStorage.getItem(RECENT_SEARCHES_KEY);
    if (stored) {
      return JSON.parse(stored);
    }
  } catch (error) {
    console.error('Error reading recent searches from localStorage:', error);
  }
  return [];
}

/**
 * Save recent search to localStorage
 */
export function saveLocalRecentSearch(search: RecentSearch): void {
  try {
    const recent = getLocalRecentSearches();
    // Remove duplicates (same query + result)
    const filtered = recent.filter(
      (s) => !(s.query === search.query && s.result_id === search.result_id && s.result_type === search.result_type)
    );
    // Add new search at the beginning
    const updated = [search, ...filtered].slice(0, MAX_LOCAL_RECENT_SEARCHES);
    localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(updated));
  } catch (error) {
    console.error('Error saving recent search to localStorage:', error);
  }
}

/**
 * Perform global search
 */
export async function searchGlobal(query: string, limit: number = 10): Promise<SearchResult[]> {
  if (!query.trim()) {
    return [];
  }

  try {
    const response = await searchAPI.globalSearch(query, limit);
    return response.data.results;
  } catch (error) {
    console.error('Error performing global search:', error);
    return [];
  }
}

/**
 * Get recent searches (from both localStorage and backend)
 */
export async function getRecentSearches(limit: number = 20): Promise<RecentSearch[]> {
  // Get from localStorage first for instant access
  const localSearches = getLocalRecentSearches();

  // Try to get from backend if authenticated
  try {
    const response = await searchAPI.getRecentSearches(limit);
    const backendSearches = response.data.results;

    // Merge and deduplicate (prefer backend for cross-device sync)
    const merged = [...backendSearches];
    for (const local of localSearches) {
      const exists = merged.some(
        (s) => s.query === local.query && s.result_id === local.result_id && s.result_type === local.result_type
      );
      if (!exists) {
        merged.push(local);
      }
    }

    // Sort by searched_at (most recent first) and limit
    return merged
      .sort((a, b) => new Date(b.searched_at).getTime() - new Date(a.searched_at).getTime())
      .slice(0, limit);
  } catch (error) {
    // If backend fails, just return local searches
    console.error('Error fetching recent searches from backend:', error);
    return localSearches.slice(0, limit);
  }
}

/**
 * Save a recent search (to both localStorage and backend)
 */
export async function saveRecentSearch(
  query: string,
  result: SearchResult
): Promise<void> {
  const recentSearch: RecentSearch = {
    id: Date.now(), // Temporary ID for local storage
    query: query.trim(),
    result_type: result.type,
    result_id: result.id,
    result_title: result.title,
    searched_at: new Date().toISOString(),
  };

  // Save to localStorage immediately
  saveLocalRecentSearch(recentSearch);

  // Try to save to backend if authenticated
  try {
    await searchAPI.saveRecentSearch({
      query: recentSearch.query,
      result_type: recentSearch.result_type,
      result_id: recentSearch.result_id,
      result_title: recentSearch.result_title,
    });
  } catch (error) {
    // If backend save fails, that's okay - we still have it in localStorage
    console.error('Error saving recent search to backend:', error);
  }
}
