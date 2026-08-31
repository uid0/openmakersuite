/**
 * Inventory List Component
 * Displays all active inventory items with search and filtering
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSwipeable } from 'react-swipeable';
import { indexCardsAPI, inventoryAPI } from '../services/api';
import '../styles/InventoryList.css';
import { InventoryItem } from '../types';
import { showError, showInfo } from '../utils/dialogs';

interface InventoryCardProps {
  item: InventoryItem;
  onSwipeLeft: (itemId: string) => void;
  onSwipeRight: (itemId: string) => void;
  onClick: (itemId: string) => void;
}

const InventoryCard: React.FC<InventoryCardProps> = ({
  item,
  onSwipeLeft,
  onSwipeRight,
  onClick,
}) => {
  const swipeHandlers = useSwipeable({
    onSwipedLeft: () => onSwipeLeft(item.id),
    onSwipedRight: () => onSwipeRight(item.id),
    delta: 50,
    trackMouse: false,
  });

  return (
    <div
      {...swipeHandlers}
      className={`inventory-card ${item.needs_reorder ? 'low-stock' : ''}`}
      onClick={() => onClick(item.id)}
    >
      {item.thumbnail && (
        <div className="item-image">
          <img src={item.thumbnail} alt={item.name} />
        </div>
      )}

      <div className="item-details">
        <h3 className="item-name">{item.name}</h3>
        <p className="item-sku">SKU: {item.sku}</p>

        {item.category_name && (
          <span className="item-category">{item.category_name}</span>
        )}

        <div className="item-stock">
          {item.use_case_based_reorder ? (
            <>
              <div className="stock-row">
                <span className="stock-label">Current Cases:</span>
                <span
                  className={`stock-value ${item.needs_reorder ? 'low' : 'good'}`}
                >
                  {item.current_cases === null
                    ? '—'
                    : item.current_cases.toFixed(1)}
                </span>
              </div>
              <div className="stock-row">
                <span className="stock-label">Minimum Cases:</span>
                <span className="stock-value">{item.minimum_cases}</span>
              </div>
              <div className="stock-row units-detail">
                <span className="stock-label">Units:</span>
                <span className="stock-value-small">{item.current_stock}</span>
              </div>
            </>
          ) : (
            <>
              <div className="stock-row">
                <span className="stock-label">Current:</span>
                <span
                  className={`stock-value ${item.needs_reorder ? 'low' : 'good'}`}
                >
                  {item.current_stock}
                </span>
              </div>
              <div className="stock-row">
                <span className="stock-label">Minimum:</span>
                <span className="stock-value">{item.minimum_stock}</span>
              </div>
            </>
          )}
        </div>

        {item.needs_reorder && (
          <div className="reorder-badge">
            ⚠️ Needs Reorder (
            {item.use_case_based_reorder
              ? `${item.reorder_cases} cases`
              : `${item.reorder_quantity} units`
            })
          </div>
        )}

        {item.has_pending_reorder && (
          <div className="pending-badge">
            🔄 Reorder in Progress
          </div>
        )}

        {item.location && (
          <div className="item-location">
            📍 {item.location}
          </div>
        )}

        {item.unit_cost && (
          <div className="item-price">
            ${parseFloat(item.unit_cost).toFixed(2)} per unit
          </div>
        )}
      </div>
    </div>
  );
};

const InventoryList: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [filter, setFilter] = useState<'all' | 'low-stock'>('all');
  const [generatingTestSheet, setGeneratingTestSheet] = useState(false);

  useEffect(() => {
    console.log('InventoryList: Component mounted, loading items...');
    loadItems();
  }, []);

  const loadItems = async () => {
    try {
      console.log('InventoryList: Making API call to /inventory/items/');
      setLoading(true);
      const response = await inventoryAPI.listItems();
      console.log('InventoryList: API response received:', response.data);
      setItems(response.data.results);
    } catch (err) {
      console.error('InventoryList: Error loading inventory:', err);
    } finally {
      setLoading(false);
    }
  };

  const filteredItems = items.filter((item) => {
    // Apply search filter
    const matchesSearch =
      item.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.sku.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (item.description && item.description.toLowerCase().includes(searchTerm.toLowerCase()));

    // Apply stock filter
    const matchesFilter =
      filter === 'all' || (filter === 'low-stock' && item.needs_reorder);

    return matchesSearch && matchesFilter;
  });

  const lowStockCount = items.filter((item) => item.needs_reorder).length;

  const handleItemClick = (itemId: string) => {
    navigate(`/inventory/items/${itemId}`);
  };

  const handleSwipeLeft = (itemId: string) => {
    handleItemClick(itemId);
  };

  const handleSwipeRight = (itemId: string) => {
    // Swipe right for quick reorder action
    navigate(`/inventory/items/${itemId}?action=reorder`);
  };

  const generateTestSheet = async () => {
    if (filteredItems.length === 0) {
      showInfo('No items to generate test sheet for.');
      return;
    }

    try {
      setGeneratingTestSheet(true);
      const itemIds = filteredItems.map((item) => item.id);
      const response = await indexCardsAPI.generateTestSheet(itemIds);

      // Create a blob URL and trigger download
      const blob = new Blob([response.data], { type: 'application/pdf' });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'test_sheet.pdf';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Error generating test sheet:', err);
      showError('Failed to generate test sheet. Please try again.');
    } finally {
      setGeneratingTestSheet(false);
    }
  };

  if (loading) {
    return <div className="loading">Loading inventory...</div>;
  }

  return (
    <div className="inventory-list-container">
      <div className="inventory-header">
        <h2>Active Inventory ({items.length} items)</h2>
        <div className="inventory-stats">
          {lowStockCount > 0 && (
            <span className="stat-badge low-stock">
              {lowStockCount} items need reordering
            </span>
          )}
        </div>
      </div>

      <div className="inventory-controls">
        <input
          type="text"
          placeholder="Search inventory..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
        />

        <div className="filter-buttons">
          <button
            className={filter === 'all' ? 'active' : ''}
            onClick={() => setFilter('all')}
          >
            All Items ({items.length})
          </button>
          <button
            className={filter === 'low-stock' ? 'active' : ''}
            onClick={() => setFilter('low-stock')}
          >
            Low Stock ({lowStockCount})
          </button>
          <button
            className="generate-test-sheet-btn"
            onClick={generateTestSheet}
            disabled={generatingTestSheet || filteredItems.length === 0}
            title="Generate 8.5x11 test sheet with QR codes for filtered items"
          >
            {generatingTestSheet ? 'Generating...' : '📄 Print Test Sheet'}
          </button>
        </div>
      </div>

      {filteredItems.length === 0 ? (
        <div className="no-results">
          {searchTerm ? 'No items match your search.' : 'No inventory items found.'}
        </div>
      ) : (
        <div className="inventory-grid">
          {filteredItems.map((item) => (
            <InventoryCard
              key={item.id}
              item={item}
              onSwipeLeft={handleSwipeLeft}
              onSwipeRight={handleSwipeRight}
              onClick={handleItemClick}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export default InventoryList;
