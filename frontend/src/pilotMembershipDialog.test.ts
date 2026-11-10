import { describe, expect, it } from "vitest";
import {
  pilotDialogDismissKey,
  pilotDialogPhaseAfterAttempt,
  pilotDialogShowConfirmAction,
} from "./pilotMembershipDialog";
import { messages } from "./i18n";

describe("pilot membership dialog buttons", () => {
  it("shows Cancel before submit and hides Close semantics", () => {
    expect(pilotDialogDismissKey("confirm")).toBe("cancel");
    expect(pilotDialogShowConfirmAction("confirm")).toBe(true);
    expect(messages.en.workstations.cancel).toBe("Cancel");
  });

  it("shows Close after success, failure, and partial bulk", () => {
    expect(pilotDialogPhaseAfterAttempt({ allSucceeded: true })).toBe("done");
    expect(pilotDialogPhaseAfterAttempt({ failed: true })).toBe("done");
    expect(pilotDialogPhaseAfterAttempt({ partial: true })).toBe("done");
    expect(pilotDialogPhaseAfterAttempt({ error: true })).toBe("done");

    expect(pilotDialogDismissKey("done")).toBe("close");
    expect(pilotDialogShowConfirmAction("done")).toBe(false);
    expect(messages.en.workstations.close).toBe("Close");
    expect(messages.ar.workstations.close).toBeTruthy();
  });

  it("Close is dismiss-only (no undo wording in label)", () => {
    expect(messages.en.workstations.close.toLowerCase()).not.toMatch(/undo|remove|revert/);
    expect(pilotDialogDismissKey("done")).not.toBe("cancel");
  });
});
