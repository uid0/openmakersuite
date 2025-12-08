/**
 * FormFileUpload - Reusable file upload component with react-hook-form integration
 */
import { Button, Group, Paper, Stack, Text } from '@mantine/core';
import { Dropzone, DropzoneProps } from '@mantine/dropzone';
import { IconFile, IconUpload, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import { Control, Controller, FieldPath, FieldValues } from 'react-hook-form';

export interface FormFileUploadProps<T extends FieldValues> extends Omit<DropzoneProps, 'name' | 'control' | 'onDrop'> {
  name: FieldPath<T>;
  control: Control<T>;
  label?: string;
  description?: string;
  required?: boolean;
  maxSize?: number;
  accept?: string[];
  multiple?: boolean;
}

export function FormFileUpload<T extends FieldValues>({
  name,
  control,
  label,
  description,
  required,
  maxSize = 10 * 1024 * 1024, // 10MB default
  accept,
  multiple = false,
  ...dropzoneProps
}: FormFileUploadProps<T>) {
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);

  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => {
        const handleDrop = (files: File[]) => {
          if (multiple) {
            const newFiles = [...uploadedFiles, ...files];
            field.onChange(newFiles);
            setUploadedFiles(newFiles);
          } else {
            const file = files[0];
            field.onChange(file);
            setUploadedFiles([file]);
          }
        };

        const handleRemove = (index: number) => {
          if (multiple) {
            const newFiles = uploadedFiles.filter((_, i) => i !== index);
            field.onChange(newFiles);
            setUploadedFiles(newFiles);
          } else {
            field.onChange(null);
            setUploadedFiles([]);
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
              accept={accept}
              maxSize={maxSize}
              multiple={multiple}
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
                  <IconFile size={52} stroke={1.5} />
                </Dropzone.Idle>

                <div>
                  <Text size="xl" inline>
                    Drag file{multiple ? 's' : ''} here or click to select
                  </Text>
                  <Text size="sm" c="dimmed" inline mt={7}>
                    Attach file{multiple ? 's' : ''} (max {Math.round(maxSize / 1024 / 1024)}MB)
                  </Text>
                </div>
              </Group>
            </Dropzone>
            {uploadedFiles.length > 0 && (
              <Stack gap="xs">
                {uploadedFiles.map((file, index) => (
                  <Paper key={index} p="xs" withBorder>
                    <Group justify="space-between">
                      <Text size="sm">{file.name}</Text>
                      <Button
                        variant="subtle"
                        color="red"
                        size="xs"
                        onClick={() => handleRemove(index)}
                      >
                        Remove
                      </Button>
                    </Group>
                  </Paper>
                ))}
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
