/**
 * Category Form Page
 * Create/Edit form for categories
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import ColorPicker from '../components/ColorPicker';
import { inventoryAPI } from '../services/api';
import '../styles/CategoryFormPage.css';
import { Category } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface CategoryFormData {
  name: string;
  description: string;
  parent: number | null;
  color: string;
}

const CategoryFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [formData, setFormData] = useState<CategoryFormData>({
    name: '',
    description: '',
    parent: null,
    color: '',
  });

  useEffect(() => {
    loadCategories();
    if (isEditMode) {
      loadCategory();
    }
  }, [id, isEditMode]);

  const loadCategories = async () => {
    try {
      const response = await inventoryAPI.listCategories();
      setCategories(response.data.results);
    } catch (err) {
      console.error('Error loading categories:', err);
    }
  };

  const loadCategory = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await inventoryAPI.listCategories();
      const category = response.data.results.find((c) => c.id.toString() === id);
      
      if (category) {
        setFormData({
          name: category.name,
          description: category.description || '',
          parent: category.parent,
          color: category.color || '',
        });
      } else {
        setError('Category not found');
      }
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load category'));
      console.error('Error loading category:', err);
    } finally {
      setLoading(false);
    }
  };

  const buildCategoryOptions = (items: Category[], excludeId?: number, level: number = 0): JSX.Element[] => {
    const options: JSX.Element[] = [];
    
    items
      .filter((item) => item.id !== excludeId)
      .forEach((item) => {
        const indent = '  '.repeat(level);
        options.push(
          <option key={item.id} value={item.id}>
            {indent}{item.name}
          </option>
        );
        
        if (item.children && item.children.length > 0) {
          options.push(...buildCategoryOptions(item.children, excludeId, level + 1));
        }
      });
    
    return options;
  };

  const categoryOptions = useMemo(() => {
    const roots = categories.filter((c) => !c.parent);
    return buildCategoryOptions(roots, isEditMode ? parseInt(id!) : undefined);
  }, [categories, id, isEditMode]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!formData.name.trim()) {
      setError('Name is required');
      return;
    }

    try {
      setSaving(true);
      setError(null);

      const data: any = {
        name: formData.name.trim(),
        description: formData.description.trim(),
        color: formData.color || '',
      };

      if (formData.parent) {
        data.parent = formData.parent;
      }

      if (isEditMode) {
        await inventoryAPI.updateCategory(id!, data);
      } else {
        await inventoryAPI.createCategory(data);
      }

      navigate('/inventory/categories');
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to save category'));
      console.error('Error saving category:', err);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="category-form-page">
        <div className="loading">Loading category...</div>
      </div>
    );
  }

  return (
    <div className="category-form-page">
      <header className="page-header">
        <h1>{isEditMode ? 'Edit Category' : 'Create Category'}</h1>
      </header>

      {error && (
        <div className="error-message">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="category-form">
        <div className="form-group">
          <label htmlFor="name">
            Name <span className="required">*</span>
          </label>
          <input
            id="name"
            type="text"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            required
            className="form-input"
            placeholder="Category name"
          />
        </div>

        <div className="form-group">
          <label htmlFor="description">Description</label>
          <textarea
            id="description"
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            className="form-textarea"
            placeholder="Category description"
            rows={4}
          />
        </div>

        <div className="form-group">
          <label htmlFor="parent">Parent Category</label>
          <select
            id="parent"
            value={formData.parent || ''}
            onChange={(e) =>
              setFormData({
                ...formData,
                parent: e.target.value ? parseInt(e.target.value) : null,
              })
            }
            className="form-select"
          >
            <option value="">None (Top Level)</option>
            {categoryOptions}
          </select>
        </div>

        <div className="form-group">
          <ColorPicker
            value={formData.color}
            onChange={(color) => setFormData({ ...formData, color })}
            label="Color"
          />
        </div>

        <div className="form-actions">
          <button
            type="button"
            onClick={() => navigate('/inventory/categories')}
            className="btn-secondary"
            disabled={saving}
          >
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? 'Saving...' : isEditMode ? 'Update Category' : 'Create Category'}
          </button>
        </div>
      </form>
    </div>
  );
};

export default CategoryFormPage;
