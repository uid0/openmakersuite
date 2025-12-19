/**
 * ColorPicker Component
 * Reusable color picker with preview swatch
 */
import React, { useState } from 'react';
import '../styles/ColorPicker.css';

interface ColorPickerProps {
  value: string;
  onChange: (color: string) => void;
  label?: string;
  showPreview?: boolean;
}

const ColorPicker: React.FC<ColorPickerProps> = ({
  value,
  onChange,
  label,
  showPreview = true,
}) => {
  // Removed unused state - was intended for future dropdown feature

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.value);
  };

  return (
    <div className="color-picker">
      {label && <label className="color-picker-label">{label}</label>}
      <div className="color-picker-input-group">
        {showPreview && (
          <div
            className="color-picker-preview"
            style={{ backgroundColor: value || '#ffffff' }}
          />
        )}
        <input
          type="color"
          value={value || '#ffffff'}
          onChange={handleChange}
          className="color-picker-input"
        />
        <input
          type="text"
          value={value || ''}
          onChange={(e) => onChange(e.target.value)}
          placeholder="#FFFFFF"
          className="color-picker-text"
          pattern="^#[0-9A-Fa-f]{6}$"
        />
      </div>
    </div>
  );
};

export default ColorPicker;
