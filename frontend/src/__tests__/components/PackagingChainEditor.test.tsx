/**
 * Tests for PackagingChainEditor (op-lkxl): add / edit / remove / reorder the
 * rungs of an item's packaging chain, and the derived "1 case = 10 reams" line
 * that makes the chain readable while it is being typed.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import PackagingChainEditor from '../../components/inventory/PackagingChainEditor';
import { PackagingRow, blankPackagingRow, validatePackagingChain } from '../../utils/packaging';

const makeRow = (name: string, base_units: number | ''): PackagingRow => ({
  ...blankPackagingRow(),
  name,
  base_units,
});

/**
 * The editor is controlled, so the harness owns the rows exactly as the item
 * form does — that is what makes "click down, assert the new order" meaningful.
 */
const Harness: React.FC<{ initial?: PackagingRow[] }> = ({ initial = [] }) => {
  const [rows, setRows] = useState<PackagingRow[]>(initial);
  return (
    <MantineProvider env="test">
      <PackagingChainEditor
        rows={rows}
        onChange={setRows}
        baseUnit="sheet"
        errors={validatePackagingChain(rows)}
      />
    </MantineProvider>
  );
};

const renderEditor = (initial?: PackagingRow[]) => render(<Harness initial={initial} />);

describe('PackagingChainEditor', () => {
  it('says the item is counted in base units when there is no chain', () => {
    renderEditor();

    expect(screen.getByTestId('packaging-chain-empty')).toHaveTextContent(
      'counted in sheets'
    );
    expect(screen.queryByTestId('packaging-chain-table')).not.toBeInTheDocument();
  });

  it('adds a level', () => {
    renderEditor();

    fireEvent.click(screen.getByTestId('packaging-add-level'));

    expect(screen.getByTestId('packaging-row-0')).toBeInTheDocument();
    expect(screen.getByTestId('packaging-row-name-0')).toHaveValue('');
  });

  it('edits a level name and size', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('packaging-add-level'));

    fireEvent.change(screen.getByTestId('packaging-row-name-0'), {
      target: { value: 'case' },
    });
    fireEvent.change(screen.getByTestId('packaging-row-base-units-0'), {
      target: { value: '500' },
    });

    expect(screen.getByTestId('packaging-row-name-0')).toHaveValue('case');
    expect(screen.getByTestId('packaging-row-base-units-0')).toHaveValue('500');
  });

  it('removes a level', () => {
    renderEditor([makeRow('case', 500), makeRow('sheet', 1)]);

    fireEvent.click(screen.getByTestId('packaging-row-remove-0'));

    expect(screen.getAllByTestId(/^packaging-row-\d+$/)).toHaveLength(1);
    expect(screen.getByTestId('packaging-row-name-0')).toHaveValue('sheet');
  });

  it('reorders levels, which is what changes their sort_order on save', () => {
    renderEditor([makeRow('case', 500), makeRow('ream', 100), makeRow('sheet', 1)]);

    fireEvent.click(screen.getByTestId('packaging-row-down-0'));

    expect(screen.getByTestId('packaging-row-name-0')).toHaveValue('ream');
    expect(screen.getByTestId('packaging-row-name-1')).toHaveValue('case');

    fireEvent.click(screen.getByTestId('packaging-row-up-1'));

    expect(screen.getByTestId('packaging-row-name-0')).toHaveValue('case');
    expect(screen.getByTestId('packaging-row-name-1')).toHaveValue('ream');
  });

  it('pins the outermost row against moving up and the innermost against moving down', () => {
    renderEditor([makeRow('case', 500), makeRow('sheet', 1)]);

    expect(screen.getByTestId('packaging-row-up-0')).toBeDisabled();
    expect(screen.getByTestId('packaging-row-down-1')).toBeDisabled();
    expect(screen.getByTestId('packaging-row-down-0')).not.toBeDisabled();
  });

  it('shows the derived "1 case = N reams" ratio, and nothing for the base rung', () => {
    renderEditor([makeRow('case', 1000), makeRow('ream', 100), makeRow('sheet', 1)]);

    expect(screen.getByTestId('packaging-row-per-parent-0')).toHaveTextContent(
      '1 case = 10 reams'
    );
    expect(screen.getByTestId('packaging-row-per-parent-1')).toHaveTextContent(
      '1 ream = 100 sheets'
    );
    expect(screen.queryByTestId('packaging-row-per-parent-2')).not.toBeInTheDocument();
  });

  it('recomputes the ratio as a size is edited', () => {
    renderEditor([makeRow('case', 1000), makeRow('sheet', 1)]);

    expect(screen.getByTestId('packaging-row-per-parent-0')).toHaveTextContent(
      '1 case = 1000 sheets'
    );

    fireEvent.change(screen.getByTestId('packaging-row-base-units-0'), {
      target: { value: '12' },
    });

    expect(screen.getByTestId('packaging-row-per-parent-0')).toHaveTextContent(
      '1 case = 12 sheets'
    );
  });

  it('surfaces the chain rules while the chain is invalid', () => {
    renderEditor([makeRow('case', 10), makeRow('ream', 10), makeRow('sheet', 1)]);

    expect(screen.getByTestId('packaging-chain-errors')).toHaveTextContent(
      /'ream' must hold fewer base units than 'case'/
    );
  });

  it('shows no errors for a valid chain', () => {
    renderEditor([makeRow('case', 1000), makeRow('sheet', 1)]);

    expect(screen.queryByTestId('packaging-chain-errors')).not.toBeInTheDocument();
  });
});
