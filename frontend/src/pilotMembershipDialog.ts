/** Pure helpers for the Workstations pilot membership confirm dialog. */

export type PilotDialogPhase = "confirm" | "done";

/** Dismiss control: Cancel before submit; Close after any completed attempt. */
export function pilotDialogDismissKey(phase: PilotDialogPhase): "cancel" | "close" {
  return phase === "done" ? "close" : "cancel";
}

/** Confirm/add/remove action is only available before a result is shown. */
export function pilotDialogShowConfirmAction(phase: PilotDialogPhase): boolean {
  return phase === "confirm";
}

/**
 * Map API outcome to dialog phase. Any terminal response (success, failure,
 * partial) keeps the dialog open in "done" so the user can read results and Close.
 */
export function pilotDialogPhaseAfterAttempt(outcome: {
  allSucceeded?: boolean;
  partial?: boolean;
  failed?: boolean;
  error?: boolean;
}): PilotDialogPhase {
  void outcome;
  return "done";
}
