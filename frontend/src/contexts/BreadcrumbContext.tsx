/**
 * Breadcrumb Context
 *
 * Lets a page register a dynamic label for the final breadcrumb segment.
 * Routes like `/assets/<uuid>/edit` then read as "Home › Assets › <Asset name>"
 * instead of "Home › Assets › Edit", without the routeMap needing to know
 * about every resource id.
 *
 * Pages call `useSetBreadcrumb(label)` once their data has loaded.
 * `<BreadcrumbProvider>` is mounted by `WorkspaceLayout`; consumers used
 * outside a provider (tests, storybook) get a safe no-op.
 */
import React, {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react';

interface BreadcrumbState {
  currentLabel: string | undefined;
  setCurrentLabel: (label: string | undefined) => void;
}

const NOOP_STATE: BreadcrumbState = {
  currentLabel: undefined,
  setCurrentLabel: () => {},
};

const BreadcrumbContext = createContext<BreadcrumbState | null>(null);

export const BreadcrumbProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [currentLabel, setLabel] = useState<string | undefined>(undefined);
  const setCurrentLabel = useCallback((label: string | undefined) => {
    setLabel(label);
  }, []);

  return (
    <BreadcrumbContext.Provider value={{ currentLabel, setCurrentLabel }}>
      {children}
    </BreadcrumbContext.Provider>
  );
};

export function useBreadcrumbState(): BreadcrumbState {
  const ctx = useContext(BreadcrumbContext);
  return ctx ?? NOOP_STATE;
}

export function useSetBreadcrumb(label: string | undefined): void {
  const { setCurrentLabel } = useBreadcrumbState();
  useEffect(() => {
    setCurrentLabel(label);
    return () => {
      setCurrentLabel(undefined);
    };
  }, [label, setCurrentLabel]);
}
