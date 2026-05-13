/**
 * Thanks Page
 * Simple thank-you page shown after an anonymous QR-scan reorder
 * submission. Auto-redirects to the homepage after 5 seconds.
 */
import { Box, Button, Container, Stack, Text, ThemeIcon, Title } from '@mantine/core';
import { IconCheck } from '@tabler/icons-react';
import React, { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

import '../styles/landing.css';

const ThanksPage: React.FC = () => {
  const navigate = useNavigate();

  useEffect(() => {
    const timer = setTimeout(() => {
      navigate('/');
    }, 5000);
    return () => clearTimeout(timer);
  }, [navigate]);

  return (
    <Box className="landing-surface" data-testid="thanks-page">
      <Container size="sm" py="xl">
        <Stack gap="lg" align="center" py="xl">
          <ThemeIcon size={72} radius="xl" color="green" variant="light">
            <IconCheck size={36} stroke={2} />
          </ThemeIcon>
          <Stack gap="xs" align="center">
            <Text component="span" className="landing-eyebrow">
              Reorder request received
            </Text>
            <Title
              className="landing-display"
              style={{ fontSize: 'clamp(1.8rem, 3vw, 2.4rem)', textAlign: 'center', margin: 0 }}
            >
              Thanks for letting us know
            </Title>
            <Text c="dimmed" ta="center" maw={420}>
              Your reorder request has been submitted. Our inventory team will review it and
              take appropriate action.
            </Text>
          </Stack>
          <Button onClick={() => navigate('/')} size="md">
            Back to home
          </Button>
          <Text c="dimmed" size="xs" ta="center">
            Redirecting automatically in a few seconds…
          </Text>
        </Stack>
      </Container>
    </Box>
  );
};

export default ThanksPage;
