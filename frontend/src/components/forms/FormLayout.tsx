/**
 * FormLayout - Standard 2-column form layout with section support
 */
import { Box, Divider, Grid, Stack, Text, Title } from '@mantine/core';
import React from 'react';

export interface FormSection {
  title?: string;
  description?: string;
  children: React.ReactNode;
}

export interface FormLayoutProps {
  children: React.ReactNode;
  sections?: FormSection[];
  spacing?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';
  columns?: number;
}

export function FormLayout({
  children,
  sections,
  spacing = 'md',
  columns = 2,
}: FormLayoutProps) {
  // If sections are provided, render them
  if (sections && sections.length > 0) {
    return (
      <Stack gap={spacing}>
        {sections.map((section, index) => (
          <Box key={index}>
            {section.title && (
              <>
                <Title order={4} mb="sm">
                  {section.title}
                </Title>
                {section.description && (
                  <Box mb="md" c="dimmed">
                    <Text size="sm">{section.description}</Text>
                  </Box>
                )}
              </>
            )}
            <Grid gutter={spacing}>
              {React.Children.map(section.children, (child, childIndex) => (
                <Grid.Col key={childIndex} span={{ base: 12, sm: 6 }}>
                  {child}
                </Grid.Col>
              ))}
            </Grid>
            {index < sections.length - 1 && <Divider my="xl" />}
          </Box>
        ))}
      </Stack>
    );
  }

  // Otherwise, render children in a 2-column grid
  return (
    <Grid gutter={spacing}>
      {React.Children.map(children, (child, index) => (
        <Grid.Col key={index} span={{ base: 12, sm: 6 }}>
          {child}
        </Grid.Col>
      ))}
    </Grid>
  );
}
