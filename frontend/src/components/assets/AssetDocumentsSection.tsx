/**
 * Asset Document Library section for the asset detail page (EAM P1.3).
 *
 * Beyond the single `manual_pdf`/`image` on an Asset, this is the per-asset
 * library of manuals, CAD sources, wiring diagrams, cut-sheets, and the
 * maker-native cut-ready reference files (DXF/SVG/G-code/STL) that live WITH
 * the machine. Current documents are grouped by category with an inline
 * preview for PDFs and images; superseded (is_current=false) versions collapse
 * behind a "previous versions" toggle so nobody follows a stale manual.
 *
 * Staff/SIG-admins (canManage) can upload documents, upload a new version of an
 * existing document (which supersedes it), and delete.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { assetDocumentsAPI } from '../../services/api';
import { AssetDocument, AssetDocumentCategory } from '../../types';

interface AssetDocumentsSectionProps {
  assetId: string;
  canManage: boolean;
}

const CATEGORY_OPTIONS: { value: AssetDocumentCategory; label: string }[] = [
  { value: 'manual', label: 'Manual / Documentation' },
  { value: 'cad_source', label: 'CAD Source' },
  { value: 'wiring_diagram', label: 'Wiring Diagram' },
  { value: 'cut_sheet_spec', label: 'Cut Sheet / Spec' },
  { value: 'cut_ready_template', label: 'Cut-Ready Template (DXF/SVG/G-code/STL)' },
  { value: 'photo', label: 'Photo' },
  { value: 'other', label: 'Other' },
];

const CATEGORY_ORDER: AssetDocumentCategory[] = CATEGORY_OPTIONS.map((o) => o.value);

const previewTarget = (doc: AssetDocument): string => doc.file_url || doc.file || '';

const isPdf = (doc: AssetDocument): boolean => /\.pdf($|\?)/i.test(previewTarget(doc));

const isImage = (doc: AssetDocument): boolean =>
  /\.(png|jpe?g|gif|webp|bmp|svg)($|\?)/i.test(previewTarget(doc));

const uploaderLabel = (doc: AssetDocument): string => doc.uploaded_by_name || 'Unknown';

const AssetDocumentsSection: React.FC<AssetDocumentsSectionProps> = ({ assetId, canManage }) => {
  const [documents, setDocuments] = useState<AssetDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showPrevious, setShowPrevious] = useState(false);

  // Upload form. When `supersedesId` is set the form uploads a NEW VERSION of
  // that document (via the supersede endpoint) rather than a brand-new doc.
  const [showForm, setShowForm] = useState(false);
  const [supersedesId, setSupersedesId] = useState<string | null>(null);
  const [formTitle, setFormTitle] = useState('');
  const [formCategory, setFormCategory] = useState<AssetDocumentCategory>('manual');
  const [formDescription, setFormDescription] = useState('');
  const [formFile, setFormFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const loadDocuments = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await assetDocumentsAPI.list({ asset: assetId });
      setDocuments(resp.data.results);
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to load documents');
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  const currentDocs = useMemo(() => documents.filter((d) => d.is_current), [documents]);
  const previousDocs = useMemo(() => documents.filter((d) => !d.is_current), [documents]);

  const groupedCurrent = useMemo(() => {
    const byCategory = new Map<AssetDocumentCategory, AssetDocument[]>();
    for (const doc of currentDocs) {
      const list = byCategory.get(doc.category) || [];
      list.push(doc);
      byCategory.set(doc.category, list);
    }
    return CATEGORY_ORDER.filter((cat) => byCategory.has(cat)).map((cat) => ({
      category: cat,
      label: CATEGORY_OPTIONS.find((o) => o.value === cat)?.label || cat,
      docs: byCategory.get(cat)!,
    }));
  }, [currentDocs]);

  const resetForm = () => {
    setSupersedesId(null);
    setFormTitle('');
    setFormCategory('manual');
    setFormDescription('');
    setFormFile(null);
    setFormError(null);
  };

  const openAddForm = () => {
    resetForm();
    setShowForm(true);
  };

  const openSupersedeForm = (doc: AssetDocument) => {
    resetForm();
    setSupersedesId(doc.id);
    setFormTitle(doc.title);
    setFormCategory(doc.category);
    setShowForm(true);
  };

  const closeForm = () => {
    resetForm();
    setShowForm(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!formFile) {
      setFormError('Please choose a file to upload.');
      return;
    }
    if (!formTitle.trim()) {
      setFormError('Please give the document a title.');
      return;
    }
    setSubmitting(true);
    try {
      if (supersedesId) {
        await assetDocumentsAPI.supersede(supersedesId, {
          file: formFile,
          title: formTitle.trim(),
          category: formCategory,
          description: formDescription.trim() || undefined,
        });
      } else {
        await assetDocumentsAPI.upload({
          asset: assetId,
          file: formFile,
          title: formTitle.trim(),
          category: formCategory,
          description: formDescription.trim() || undefined,
        });
      }
      closeForm();
      await loadDocuments();
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail ||
        err?.response?.data?.supersedes?.[0] ||
        err?.message ||
        'Failed to upload document';
      setFormError(detail);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (doc: AssetDocument) => {
    if (!window.confirm(`Delete "${doc.title}" (v${doc.version})? This cannot be undone.`)) {
      return;
    }
    try {
      await assetDocumentsAPI.delete(doc.id);
      await loadDocuments();
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to delete document');
    }
  };

  const renderPreview = (doc: AssetDocument) => {
    if (!doc.file_url) return null;
    if (isImage(doc)) {
      return (
        <img
          src={doc.file_url}
          alt={doc.title}
          style={{ maxWidth: '100%', maxHeight: 240, borderRadius: 4, marginTop: '0.5rem' }}
        />
      );
    }
    if (isPdf(doc)) {
      return (
        <iframe
          title={`${doc.title} (PDF preview)`}
          src={doc.file_url}
          style={{ width: '100%', height: 360, border: '1px solid #cdd9e4', marginTop: '0.5rem' }}
        />
      );
    }
    return null;
  };

  const renderDocRow = (doc: AssetDocument, opts: { previous?: boolean } = {}) => (
    <div
      key={doc.id}
      data-testid={`asset-document-row-${doc.id}`}
      style={{
        border: '1px solid #e2e8f0',
        borderRadius: 6,
        padding: '0.75rem',
        marginBottom: '0.75rem',
        opacity: opts.previous ? 0.6 : 1,
        background: opts.previous ? '#f7fafc' : '#fff',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', flexWrap: 'wrap' }}>
        <div>
          <strong>{doc.title}</strong>{' '}
          <span style={{ color: '#64748b', fontSize: '0.85rem' }}>
            · {doc.category_display} · v{doc.version}
            {opts.previous ? ' · superseded' : ''}
          </span>
          <div style={{ color: '#64748b', fontSize: '0.8rem' }}>Uploaded by {uploaderLabel(doc)}</div>
          {doc.description && (
            <div style={{ fontSize: '0.85rem', marginTop: '0.25rem' }}>{doc.description}</div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem', flexWrap: 'wrap' }}>
          {doc.file_url && (
            <a
              href={doc.file_url}
              target="_blank"
              rel="noopener noreferrer"
              className="resource-link"
              data-testid={`asset-document-download-${doc.id}`}
            >
              Download
            </a>
          )}
          {canManage && !opts.previous && (
            <button
              type="button"
              className="action-button"
              onClick={() => openSupersedeForm(doc)}
              data-testid={`asset-document-supersede-${doc.id}`}
              style={{ padding: '0.25rem 0.6rem', fontSize: '0.8rem' }}
            >
              New version
            </button>
          )}
          {canManage && (
            <button
              type="button"
              className="action-button"
              onClick={() => handleDelete(doc)}
              data-testid={`asset-document-delete-${doc.id}`}
              style={{ padding: '0.25rem 0.6rem', fontSize: '0.8rem' }}
            >
              Delete
            </button>
          )}
        </div>
      </div>
      {!opts.previous && renderPreview(doc)}
    </div>
  );

  return (
    <section className="asset-detail-section" data-testid="asset-documents-section">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '1rem',
          flexWrap: 'wrap',
          gap: '0.5rem',
        }}
      >
        <h2 style={{ margin: 0 }}>Documents</h2>
        {canManage && !showForm && (
          <button
            type="button"
            className="action-button"
            onClick={openAddForm}
            data-testid="asset-documents-add"
            style={{ padding: '0.4rem 0.85rem' }}
          >
            + Add document
          </button>
        )}
      </div>

      {canManage && showForm && (
        <form
          onSubmit={handleSubmit}
          data-testid="asset-documents-form"
          style={{
            border: '1px solid #cdd9e4',
            borderRadius: 6,
            padding: '1rem',
            marginBottom: '1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.75rem',
          }}
        >
          {supersedesId && (
            <div data-testid="asset-documents-supersede-banner" style={{ fontSize: '0.85rem' }}>
              Uploading a new version — this will supersede the current document.
            </div>
          )}
          <div>
            <label htmlFor="asset-doc-title" style={{ display: 'block', fontSize: '0.85rem' }}>
              Title
            </label>
            <input
              id="asset-doc-title"
              type="text"
              value={formTitle}
              onChange={(e) => setFormTitle(e.target.value)}
              data-testid="asset-document-title"
              required
            />
          </div>
          <div>
            <label htmlFor="asset-doc-category" style={{ display: 'block', fontSize: '0.85rem' }}>
              Category
            </label>
            <select
              id="asset-doc-category"
              value={formCategory}
              onChange={(e) => setFormCategory(e.target.value as AssetDocumentCategory)}
              data-testid="asset-document-category"
            >
              {CATEGORY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="asset-doc-description" style={{ display: 'block', fontSize: '0.85rem' }}>
              Description (optional)
            </label>
            <textarea
              id="asset-doc-description"
              value={formDescription}
              onChange={(e) => setFormDescription(e.target.value)}
              data-testid="asset-document-description"
              rows={2}
            />
          </div>
          <div>
            <label htmlFor="asset-doc-file" style={{ display: 'block', fontSize: '0.85rem' }}>
              File
            </label>
            <input
              id="asset-doc-file"
              type="file"
              onChange={(e) => setFormFile(e.target.files?.[0] ?? null)}
              data-testid="asset-document-file"
            />
          </div>
          {formError && (
            <div role="alert" style={{ color: '#c0392b', fontSize: '0.85rem' }}>
              {formError}
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              type="submit"
              className="action-button"
              disabled={submitting}
              data-testid="asset-document-submit"
            >
              {submitting ? 'Uploading…' : 'Upload'}
            </button>
            <button type="button" className="action-button" onClick={closeForm} disabled={submitting}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {loading && <p data-testid="asset-documents-loading">Loading documents…</p>}
      {error && (
        <p role="alert" style={{ color: '#c0392b' }}>
          {error}
        </p>
      )}

      {!loading && !error && currentDocs.length === 0 && (
        <p data-testid="asset-documents-empty" style={{ color: '#64748b' }}>
          No documents yet.
        </p>
      )}

      {!loading &&
        !error &&
        groupedCurrent.map((group) => (
          <div key={group.category} data-testid={`asset-documents-group-${group.category}`}>
            <h3 style={{ marginBottom: '0.5rem' }}>{group.label}</h3>
            {group.docs.map((doc) => renderDocRow(doc))}
          </div>
        ))}

      {!loading && !error && previousDocs.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowPrevious((v) => !v)}
            aria-expanded={showPrevious}
            data-testid="asset-documents-previous-toggle"
            style={{
              background: 'none',
              border: 'none',
              color: '#2b6cb0',
              cursor: 'pointer',
              padding: '0.25rem 0',
              fontSize: '0.9rem',
            }}
          >
            {showPrevious
              ? `Hide previous versions (${previousDocs.length})`
              : `Show previous versions (${previousDocs.length})`}
          </button>
          {showPrevious && (
            <div data-testid="asset-documents-previous-list">
              {previousDocs.map((doc) => renderDocRow(doc, { previous: true }))}
            </div>
          )}
        </div>
      )}
    </section>
  );
};

export default AssetDocumentsSection;
