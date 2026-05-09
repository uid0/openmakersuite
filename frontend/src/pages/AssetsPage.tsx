/**
 * Assets Page
 * Displays all hard assets with search and filtering
 */
import React from 'react';
import AssetList from '../components/AssetList';
import WorkspacePage from '../components/landing/WorkspacePage';

const AssetsPage: React.FC = () => (
  <WorkspacePage
    testId="assets-page"
    hero={{
      eyebrow: 'Assets',
      title: 'Asset inventory',
      description: 'Every piece of equipment in the makerspace — lifecycle, ownership, and maintenance status.',
    }}
  >
    <AssetList />
  </WorkspacePage>
);

export default AssetsPage;

