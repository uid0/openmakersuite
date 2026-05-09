/**
 * Category List Page
 * Display categories in tree view with color picker and item counts
 */
import { Button, Paper, Stack, Text, TextInput } from '@mantine/core';
import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import TreeView, { TreeNode } from '../components/TreeView';
import { inventoryAPI } from '../services/api';
import '../styles/CategoryListPage.css';
import { Category } from '../types';
import { confirmDelete, showError } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const CategoryListPage: React.FC = () => {
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    loadCategories();
  }, []);

  const loadCategories = async () => {
    try {
      setLoading(true);
      const response = await inventoryAPI.listCategories();
      setCategories(response.data.results);
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load categories'));
      console.error('Error loading categories:', err);
    } finally {
      setLoading(false);
    }
  };

  const buildTree = (items: Category[]): TreeNode[] => {
    const itemMap = new Map<number, TreeNode>();
    const roots: TreeNode[] = [];

    // First pass: create all nodes
    items.forEach((item) => {
      itemMap.set(item.id, {
        ...item,
      });
    });

    // Second pass: build tree structure
    items.forEach((item) => {
      const node = itemMap.get(item.id)!;
      if (item.parent === null) {
        roots.push(node);
      } else {
        const parent = itemMap.get(item.parent);
        if (parent) {
          if (!parent.children) {
            parent.children = [];
          }
          parent.children.push(node);
        } else {
          // Parent not found, treat as root
          roots.push(node);
        }
      }
    });

    return roots;
  };

  const filteredCategories = useMemo(() => {
    if (!searchTerm) return categories;
    const term = searchTerm.toLowerCase();
    return categories.filter(
      (cat) =>
        cat.name.toLowerCase().includes(term) ||
        cat.description?.toLowerCase().includes(term)
    );
  }, [categories, searchTerm]);

  const treeNodes = useMemo(() => buildTree(filteredCategories), [filteredCategories]);

  const handleDelete = (id: number) => {
    confirmDelete('Are you sure you want to delete this category?', async () => {
      try {
        await inventoryAPI.deleteCategory(id.toString());
        await loadCategories();
      } catch (err: any) {
        showError(extractErrorMessage(err, 'Failed to delete category'));
      }
    });
  };

  const renderCategoryNode = (node: TreeNode, level: number) => {
    const category = node as unknown as Category;
    return (
      <div className="category-row">
        <div className="category-info">
          <div
            className="category-color-swatch"
            style={{
              backgroundColor: category.color || '#e0e0e0',
            }}
            title={category.color || 'No color'}
          />
          <div className="category-details">
            <div className="category-name">{category.name}</div>
            {category.description && (
              <div className="category-description">{category.description}</div>
            )}
          </div>
        </div>
        <div className="category-meta">
          <span className="category-item-count">
            {category.item_count || 0} items
          </span>
        </div>
        <div className="category-actions">
          <Link
            to={`/inventory/categories/${category.id}/edit`}
            className="btn-edit"
          >
            Edit
          </Link>
          <button
            onClick={() => handleDelete(category.id)}
            className="btn-delete"
          >
            Delete
          </button>
        </div>
      </div>
    );
  };

  const heroDescription = loading
    ? 'Loading…'
    : `${treeNodes.length} top-level categor${treeNodes.length === 1 ? 'y' : 'ies'} in the tree.`;

  return (
    <WorkspacePage
      testId="category-list-page"
      hero={{
        eyebrow: 'Inventory',
        title: 'Categories',
        description: heroDescription,
        action: (
          <Button component={Link} to="/inventory/categories/new">
            Create category
          </Button>
        ),
      }}
    >
      {error && (
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error}</Text>
        </Paper>
      )}

      <Paper withBorder p="md">
        <TextInput
          placeholder="Search categories…"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </Paper>

      {loading ? (
        <Paper withBorder p="md">
          <Text c="dimmed">Loading categories…</Text>
        </Paper>
      ) : treeNodes.length === 0 ? (
        <Paper withBorder p="xl" radius="md">
          <Stack gap="sm" align="center">
            <Text fw={500}>No categories found.</Text>
            <Button component={Link} to="/inventory/categories/new">
              Create first category
            </Button>
          </Stack>
        </Paper>
      ) : (
        <Paper withBorder p="md" radius="md" className="category-tree">
          <TreeView
            nodes={treeNodes}
            renderNode={renderCategoryNode}
            defaultExpanded={true}
          />
        </Paper>
      )}
    </WorkspacePage>
  );
};

export default CategoryListPage;
