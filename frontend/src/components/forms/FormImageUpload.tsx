/**
 * FormImageUpload - Reusable image upload component with react-hook-form integration
 */
import { Button, Group, Image, Stack, Text } from '@mantine/core';
import { Dropzone, DropzoneProps, IMAGE_MIME_TYPE } from '@mantine/dropzone';
import { IconPhoto, IconUpload, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import { Control, Controller, FieldPath, FieldValues } from 'react-hook-form';

export interface FormImageUploadProps<T extends FieldValues> extends Omit<DropzoneProps, 'name' | 'control' | 'onDrop'> {
  name: FieldPath<T>;
  control: Control<T>;
  label?: string;
  description?: string;
  required?: boolean;
  maxSize?: number;
  preview?: boolean;
}

export function FormImageUpload<T extends FieldValues>({
  name,
  control,
  label,
  description,
  required,
  maxSize = 5 * 1024 * 1024, // 5MB default
  preview = true,
  ...dropzoneProps
}: FormImageUploadProps<T>) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => {
        const handleDrop = (files: File[]) => {
          if (files.length > 0) {
            const file = files[0];
            field.onChange(file);
            if (preview) {
              const url = URL.createObjectURL(file);
              setPreviewUrl(url);
            }
          }
        };

        const handleRemove = () => {
          field.onChange(null);
          if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
            setPreviewUrl(null);
          }
        };

        return (
          <Stack gap="xs">
            {label && (
              <Text size="sm" fw={500}>
                {label}
                {required && <span style={{ color: 'red' }}> *</span>}
              </Text>
            )}
            {description && (
              <Text size="xs" c="dimmed">
                {description}
              </Text>
            )}
            <Dropzone
              {...dropzoneProps}
              onDrop={handleDrop}
              accept={IMAGE_MIME_TYPE}
              maxSize={maxSize}
              multiple={false}
              disabled={dropzoneProps.disabled}
            >
              <Group justify="center" gap="xl" mih={220} style={{ pointerEvents: 'none' }}>
                <Dropzone.Accept>
                  <IconUpload size={52} stroke={1.5} />
                </Dropzone.Accept>
                <Dropzone.Reject>
                  <IconX size={52} stroke={1.5} />
                </Dropzone.Reject>
                <Dropzone.Idle>
                  <IconPhoto size={52} stroke={1.5} />
                </Dropzone.Idle>

                <div>
                  <Text size="xl" inline>
                    Drag image here or click to select
                  </Text>
                  <Text size="sm" c="dimmed" inline mt={7}>
                    Attach a single image file (max {Math.round(maxSize / 1024 / 1024)}MB)
                  </Text>
                </div>
              </Group>
            </Dropzone>
            {preview && previewUrl && (
              <Stack gap="xs">
                <Image src={previewUrl} alt="Preview" height={200} fit="contain" />
                <Button variant="outline" color="red" size="xs" onClick={handleRemove}>
                  Remove Image
                </Button>
              </Stack>
            )}
            {error && (
              <Text size="sm" c="red">
                {error.message}
              </Text>
            )}
          </Stack>
        );
      }}
    />
  );
}
