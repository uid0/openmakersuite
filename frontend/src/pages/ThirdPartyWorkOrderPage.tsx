/**
 * Third-party Work Order detail page (Phase 3 stepper).
 *
 * Renders the WO as a 7-step Mantine Stepper. Each step shows:
 *   - what action is required to advance
 *   - which gates currently block advancement
 *   - the action button(s) tied to that step
 *
 * The backend's `workflow` block on the serialized WO drives gate visibility.
 */
import {
  Alert,
  Badge,
  Button,
  Card,
  Container,
  Group,
  Loader,
  NumberInput,
  Select,
  Stack,
  Stepper,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconCheck,
  IconClock,
  IconCurrencyDollar,
  IconFileInvoice,
  IconPlayerPlay,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  thirdPartyMaintenanceAPI,
  ThirdPartyWorkOrderDto,
  ThirdPartyWorkOrderStatus,
} from '../services/api';

const STATUS_TO_STEP: Record<ThirdPartyWorkOrderStatus, number> = {
  requested: 0,
  sourcing: 1,
  scheduled: 2,
  in_progress: 3,
  validated: 4,
  financial_review: 5,
  closed: 6,
  cancelled: 6,
};

const ThirdPartyWorkOrderPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [wo, setWo] = useState<ThirdPartyWorkOrderDto | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [nteInput, setNteInput] = useState<string | number | ''>('');
  const [waiverReason, setWaiverReason] = useState('');
  const [emergencyReason, setEmergencyReason] = useState('');
  const [keyfobInput, setKeyfobInput] = useState('');
  const [invoiceTotal, setInvoiceTotal] = useState<string | number | ''>('');
  const [dispatchFee, setDispatchFee] = useState<string | number | ''>('');
  const [quoteVendor, setQuoteVendor] = useState<string | null>(null);
  const [quoteAmount, setQuoteAmount] = useState<string | number | ''>('');

  const fetchWo = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const resp = await thirdPartyMaintenanceAPI.retrieve(id);
      setWo(resp.data);
      setError(null);
    } catch (err) {
      setError(
        ((err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail as string) || 'Failed to load work order',
      );
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchWo();
  }, [fetchWo]);

  const activeStep = useMemo(() => (wo ? STATUS_TO_STEP[wo.status] : 0), [wo]);

  const runAction = useCallback(
    async (fn: () => Promise<unknown>) => {
      setActing(true);
      setActionError(null);
      try {
        await fn();
        await fetchWo();
      } catch (err) {
        const detail = (err as { response?: { data?: { detail?: unknown } } })
          ?.response?.data?.detail;
        setActionError(
          typeof detail === 'string' ? detail : 'Action failed — see server logs',
        );
      } finally {
        setActing(false);
      }
    },
    [fetchWo],
  );

  if (loading && !wo) {
    return (
      <Container size="md" py="md">
        <Loader />
      </Container>
    );
  }

  if (error || !wo) {
    return (
      <Container size="md" py="md">
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {error ?? 'Work order not found'}
        </Alert>
      </Container>
    );
  }

  const wf = wo.workflow;

  const renderHeader = (
    <Group justify="space-between" align="flex-start" mb="md">
      <Stack gap={4}>
        <Title order={2}>
          {wo.short_id} — {wo.title}
        </Title>
        <Text c="dimmed">
          {wo.vendor_name} · {wo.work_type_display}
          {wo.is_emergency && ' · Emergency'}
        </Text>
      </Stack>
      <Group gap="xs">
        <Badge color="blue" size="lg">
          {wo.status_display}
        </Badge>
        {wo.warranty_recovery && (
          <Badge color="orange" variant="filled">
            Warranty Recovery
          </Badge>
        )}
        {wf.has_active_emergency_authorization && (
          <Badge color="red" variant="filled">
            Emergency Auth
          </Badge>
        )}
      </Group>
    </Group>
  );

  // ---- Step 1: Intake (NTE / emergency) ---------------------------------
  const stepIntake = (
    <Stack gap="sm">
      <Text>
        Set the Not-To-Exceed amount, or have Logistics authorize an emergency
        bypass before sourcing.
      </Text>
      <Group align="flex-end">
        <NumberInput
          label="NTE amount"
          value={nteInput}
          onChange={setNteInput}
          min={0}
          decimalScale={2}
          fixedDecimalScale
          prefix="$"
        />
        <Button
          loading={acting}
          disabled={nteInput === '' || nteInput === null}
          onClick={() =>
            runAction(() =>
              thirdPartyMaintenanceAPI.setNte(wo.id, String(nteInput)),
            )
          }
        >
          Set NTE
        </Button>
      </Group>
      <Group align="flex-end">
        <TextInput
          label="Emergency reason"
          value={emergencyReason}
          onChange={(e) => setEmergencyReason(e.currentTarget.value)}
          placeholder="e.g. weekend leak"
          flex={1}
        />
        <Button
          color="red"
          variant="outline"
          loading={acting}
          onClick={() =>
            runAction(() =>
              thirdPartyMaintenanceAPI.authorizeEmergency(wo.id, emergencyReason),
            )
          }
        >
          Authorize Emergency
        </Button>
      </Group>
      <Button
        leftSection={<IconPlayerPlay size={16} />}
        loading={acting}
        disabled={!wf.has_nte && !wf.has_active_emergency_authorization}
        onClick={() =>
          runAction(() => thirdPartyMaintenanceAPI.advanceToSourcing(wo.id))
        }
      >
        Advance to Sourcing
      </Button>
      {!wf.has_nte && !wf.has_active_emergency_authorization && (
        <Text size="sm" c="dimmed">
          Blocked: needs an NTE or active emergency authorization.
        </Text>
      )}
    </Stack>
  );

  // ---- Step 2: Sourcing (3 quotes) -------------------------------------
  const stepSourcing = (
    <Stack gap="sm">
      <Text>
        Standard work orders require <strong>3 vendor quotes</strong> or a
        signed waiver before scheduling.
      </Text>
      <Card withBorder padding="sm">
        <Text fw={600} mb="xs">
          Quotes ({wf.quote_count})
        </Text>
        {wo.quotes.length === 0 ? (
          <Text size="sm" c="dimmed">
            No quotes yet.
          </Text>
        ) : (
          <Stack gap={4}>
            {wo.quotes.map((q) => (
              <Group key={q.id} justify="space-between">
                <Text size="sm">{q.vendor_name}</Text>
                <Text size="sm">${q.amount}</Text>
              </Group>
            ))}
          </Stack>
        )}
        <Group align="flex-end" mt="md">
          <Select
            label="Vendor"
            value={quoteVendor}
            onChange={setQuoteVendor}
            data={[{ value: wo.vendor, label: wo.vendor_name }]}
            placeholder="Select vendor"
            allowDeselect={false}
          />
          <NumberInput
            label="Amount"
            value={quoteAmount}
            onChange={setQuoteAmount}
            min={0}
            decimalScale={2}
            fixedDecimalScale
            prefix="$"
          />
          <Button
            disabled={!quoteVendor || quoteAmount === '' || quoteAmount === null}
            loading={acting}
            onClick={() =>
              runAction(() =>
                thirdPartyMaintenanceAPI.addQuote({
                  work_order: wo.id,
                  vendor: quoteVendor as string,
                  amount: String(quoteAmount),
                }),
              )
            }
          >
            Add Quote
          </Button>
        </Group>
      </Card>
      <Card withBorder padding="sm">
        <Text fw={600} mb="xs">
          Waive 3-quote requirement (audit-logged)
        </Text>
        <Textarea
          value={waiverReason}
          onChange={(e) => setWaiverReason(e.currentTarget.value)}
          placeholder="Reason for waiver — required for audit"
          autosize
          minRows={2}
        />
        <Button
          mt="xs"
          variant="outline"
          loading={acting}
          disabled={!waiverReason.trim()}
          onClick={() =>
            runAction(() =>
              thirdPartyMaintenanceAPI.waiveQuoteRequirement(wo.id, waiverReason),
            )
          }
        >
          Sign Waiver
        </Button>
      </Card>
      <Button
        leftSection={<IconPlayerPlay size={16} />}
        loading={acting}
        disabled={!wf.has_required_quotes}
        onClick={() =>
          runAction(() => thirdPartyMaintenanceAPI.advanceToScheduled(wo.id))
        }
      >
        Advance to Scheduled
      </Button>
      {!wf.has_required_quotes && (
        <Text size="sm" c="dimmed">
          Blocked: need 3 quotes, a signed waiver, or active emergency
          authorization.
        </Text>
      )}
    </Stack>
  );

  // ---- Step 3: Scheduled (Vendor Arrived) ------------------------------
  const stepScheduled = (
    <Stack gap="sm">
      <Text>
        Vendor is scheduled. When the vendor checks in on-site, click{' '}
        <strong>Vendor Arrived</strong> to start the downtime clock and capture
        the keyfob assignment.
      </Text>
      <TextInput
        label="Keyfob ID"
        value={keyfobInput}
        onChange={(e) => setKeyfobInput(e.currentTarget.value)}
        placeholder="KF-####"
      />
      <Button
        leftSection={<IconClock size={16} />}
        loading={acting}
        onClick={() =>
          runAction(() =>
            thirdPartyMaintenanceAPI.vendorArrived(wo.id, {
              keyfob_id: keyfobInput || undefined,
            }),
          )
        }
      >
        Vendor Arrived
      </Button>
    </Stack>
  );

  // ---- Step 4: In-progress (Ops sign-off) -----------------------------
  const stepInProgress = (
    <Stack gap="sm">
      <Text>
        Vendor is on-site. Upload at least one photo of the work, then have Ops
        sign off to stop the downtime clock.
      </Text>
      <Group gap="md">
        <Badge color={wf.has_photo_evidence ? 'green' : 'red'}>
          {wf.has_photo_evidence
            ? 'Photo evidence ✓'
            : 'Photo evidence required'}
        </Badge>
        {wo.downtime_start && (
          <Text size="sm" c="dimmed">
            Started {new Date(wo.downtime_start).toLocaleString()}
          </Text>
        )}
      </Group>
      <Button
        leftSection={<IconCheck size={16} />}
        loading={acting}
        disabled={!wf.has_photo_evidence}
        onClick={() => runAction(() => thirdPartyMaintenanceAPI.signOff(wo.id))}
      >
        Ops Sign-Off
      </Button>
    </Stack>
  );

  // ---- Step 5: Validated (capture invoice) -----------------------------
  const stepValidated = (
    <Stack gap="sm">
      <Text>
        Work has been validated. Enter the actual invoice total and any
        dispatch (truck-roll) fee. Variance is checked against NTE on advance.
      </Text>
      {wo.total_downtime && (
        <Text size="sm" c="dimmed">
          Total downtime: {wo.total_downtime}
        </Text>
      )}
      <Group align="flex-end">
        <NumberInput
          label="Actual invoice total"
          value={invoiceTotal}
          onChange={setInvoiceTotal}
          min={0}
          decimalScale={2}
          fixedDecimalScale
          prefix="$"
        />
        <NumberInput
          label="Dispatch fee"
          value={dispatchFee}
          onChange={setDispatchFee}
          min={0}
          decimalScale={2}
          fixedDecimalScale
          prefix="$"
        />
        <Button
          leftSection={<IconCurrencyDollar size={16} />}
          loading={acting}
          disabled={invoiceTotal === '' || invoiceTotal === null}
          onClick={() =>
            runAction(() =>
              thirdPartyMaintenanceAPI.advanceToFinancialReview(wo.id, {
                actual_invoice_total: String(invoiceTotal),
                dispatch_fee:
                  dispatchFee === '' || dispatchFee === null
                    ? undefined
                    : String(dispatchFee),
              }),
            )
          }
        >
          Advance to Financial Review
        </Button>
      </Group>
    </Stack>
  );

  // ---- Step 6: Financial review (variance + closure) ------------------
  const variance = (() => {
    if (!wo.actual_invoice_total || !wo.nte_amount) return null;
    return (Number(wo.actual_invoice_total) - Number(wo.nte_amount)).toFixed(2);
  })();

  const stepFinancialReview = (
    <Stack gap="sm">
      {wo.variance_status === 'auto_approved' && (
        <Alert color="green" icon={<IconCheck size={16} />}>
          Variance auto-approved within par buffer.
        </Alert>
      )}
      {wo.variance_status === 'blocked' && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          Variance over budget — closure blocked. SIG Leader + Finance have been
          notified. Adjust the invoice or escalate to Finance.
        </Alert>
      )}
      <Group gap="md">
        <Text size="sm">NTE: ${wo.nte_amount ?? '—'}</Text>
        <Text size="sm">Actual: ${wo.actual_invoice_total ?? '—'}</Text>
        {variance && <Text size="sm">Variance: ${variance}</Text>}
      </Group>
      {wo.asset_links.length > 0 && (
        <Card withBorder padding="sm">
          <Text fw={600} mb="xs">
            Per-asset cost split
          </Text>
          <Stack gap={4}>
            {wo.asset_links.map((link) => (
              <Group key={link.id} justify="space-between">
                <Text size="sm">{link.asset_name || link.asset}</Text>
                <Text size="sm">
                  {link.share_pct}% · ${link.allocated_cost ?? '—'}
                </Text>
              </Group>
            ))}
          </Stack>
        </Card>
      )}
      <Group gap="md">
        <Badge color={wf.has_invoice_and_fsr ? 'green' : 'red'}>
          {wf.has_invoice_and_fsr
            ? 'Invoice + FSR ✓'
            : 'Need Invoice + FSR attachments'}
        </Badge>
      </Group>
      <Button
        leftSection={<IconFileInvoice size={16} />}
        loading={acting}
        color="green"
        disabled={
          wo.variance_status === 'blocked' || !wf.has_invoice_and_fsr
        }
        onClick={() => runAction(() => thirdPartyMaintenanceAPI.close(wo.id))}
      >
        Close Work Order
      </Button>
    </Stack>
  );

  const stepClosed = (
    <Alert color="green" icon={<IconCheck size={16} />}>
      Work order closed
      {wo.closed_at && ` on ${new Date(wo.closed_at).toLocaleString()}`}.
    </Alert>
  );

  return (
    <Container size="md" py="md">
      {renderHeader}
      {actionError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
          {actionError}
        </Alert>
      )}
      <Stepper active={activeStep} orientation="vertical">
        <Stepper.Step label="Intake" description="Set NTE">
          {stepIntake}
        </Stepper.Step>
        <Stepper.Step label="Sourcing" description="3 quotes / waiver">
          {stepSourcing}
        </Stepper.Step>
        <Stepper.Step label="Scheduled" description="Notify Ops">
          {stepScheduled}
        </Stepper.Step>
        <Stepper.Step label="In Progress" description="Photo + downtime">
          {stepInProgress}
        </Stepper.Step>
        <Stepper.Step label="Validated" description="Capture invoice">
          {stepValidated}
        </Stepper.Step>
        <Stepper.Step label="Financial Review" description="Variance gate">
          {stepFinancialReview}
        </Stepper.Step>
        <Stepper.Step label="Closed" description="WO complete">
          {stepClosed}
        </Stepper.Step>
      </Stepper>
    </Container>
  );
};

export default ThirdPartyWorkOrderPage;
