/**
 * Thanks Page
 * Where an anonymous QR-scan reorder ends. Auto-redirects to the homepage
 * after 5 seconds.
 *
 * TWO outcomes land here, and they are worded differently. The server files at
 * most one PENDING anonymous request per item, so a scan either filed a
 * request or named one that was already recorded — and `ScanPage` passes which
 * in `location.state.alreadyRequested`. Both are successes: in both, the need
 * is on file and there is nothing more for the member to do. What neither may
 * read as is a failure, and what "already recorded" may not read as is a
 * second request having been filed.
 *
 * A member who arrives with no state (a direct visit, a reload, a back
 * navigation) gets the plain wording — it claims nothing about which outcome
 * this was beyond "your request is in", which is true of both.
 */
import { Box, Button, Container, Stack, Text, ThemeIcon, Title } from '@mantine/core';
import { IconCheck, IconClipboardCheck } from '@tabler/icons-react';
import React, { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import '../styles/landing.css';

interface ThanksState {
  alreadyRequested?: boolean;
}

const ThanksPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const alreadyRequested = !!(location.state as ThanksState | null)?.alreadyRequested;

  useEffect(() => {
    const timer = setTimeout(() => {
      navigate('/');
    }, 5000);
    return () => clearTimeout(timer);
  }, [navigate]);

  // "Already recorded" says the need is on file and that nothing was filed
  // twice — in that order, because the first half is what the member came for
  // and the second is only reassurance. It never says "we could not", and it
  // never asks them to act again.
  const eyebrow = alreadyRequested ? 'Already on the list' : 'Reorder request received';
  const heading = alreadyRequested
    ? 'This one is already recorded'
    : 'Thanks for letting us know';
  const body = alreadyRequested
    ? 'A reorder request for this item is already open, so we did not add a second one. ' +
      'Your need is recorded and our inventory team can see it — nothing more is needed from you.'
    : 'Your reorder request has been submitted. Our inventory team will review it and ' +
      'take appropriate action.';

  return (
    <Box className="landing-surface" data-testid="thanks-page">
      <Container size="sm" py="xl">
        <Stack gap="lg" align="center" py="xl">
          <ThemeIcon
            size={72}
            radius="xl"
            color={alreadyRequested ? 'blue' : 'green'}
            variant="light"
          >
            {alreadyRequested ? (
              <IconClipboardCheck size={36} stroke={2} />
            ) : (
              <IconCheck size={36} stroke={2} />
            )}
          </ThemeIcon>
          <Stack gap="xs" align="center">
            <Text component="span" className="landing-eyebrow">
              {eyebrow}
            </Text>
            <Title
              className="landing-display"
              style={{ fontSize: 'clamp(1.8rem, 3vw, 2.4rem)', textAlign: 'center', margin: 0 }}
            >
              {heading}
            </Title>
            <Text c="dimmed" ta="center" maw={420} data-testid="thanks-body">
              {body}
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
