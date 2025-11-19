/**
 * Assets Page
 * Displays all hard assets with search and filtering
 */
import React from 'react';
import AssetList from '../components/AssetList';
import '../styles/AssetsPage.css';

const AssetsPage: React.FC = () => {
  return (
    <div className="assets-page">
      <div className="assets-header">
        <h1>Asset Inventory</h1>
        <p className="subtitle">Browse all hard assets in the makerspace</p>
      </div>
      <AssetList />
    </div>
  );
};

export default AssetsPage;

