import { describe, expect, it } from "vitest";
import { messages } from "./i18n";

describe("i18n", () => {
  it("has required English navigation keys", () => {
    expect(messages.en.nav.dashboard).toBeTruthy();
    expect(messages.en.nav.documents).toBeTruthy();
    expect(messages.en.session.google).toBeTruthy();
    expect(messages.en.login.title).toBeTruthy();
    expect(messages.en.session.username).toBeTruthy();
    expect(messages.en.session.expired).toBeTruthy();
    expect(messages.en.access.allowlist).toBeTruthy();
    expect(messages.en.synology.title).toBeTruthy();
    expect(messages.ar.nav.dashboard).toBeTruthy();
  });
});
