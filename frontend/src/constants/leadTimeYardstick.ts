/**
 * The one place the web spells the promise every lead-time number is scored
 * against.
 *
 * `variance_days` and every rate derived from it measure the supplier link's
 * STANDING QUOTED lead time, never the delivery date the operator confirmed on
 * the order, so a vendor that quotes 3, is confirmed for day 10 and delivers on
 * day 10 is 7 over its quote having hit the date it agreed. Labels that restate
 * that yardstick from memory drift apart; these constants mirror the backend's
 * single source, `LeadTimeLog.VARIANCE_YARDSTICK_LABEL`.
 */

/** The yardstick in running prose: "…days vs. quoted lead time". */
export const YARDSTICK_LABEL = 'quoted lead time';

/** The same words as a column or card heading: "Avg Quoted Lead Time (days)". */
export const YARDSTICK_LABEL_TITLE = YARDSTICK_LABEL.replace(/\b\w/g, (c) => c.toUpperCase());
