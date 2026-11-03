/** Open authorized Synology originals in the Windows registered desktop app. */

import { api } from "@/api";

export type DesktopOpenResult = {
  ok: boolean;
  denied?: boolean;
  error?: string | null;
  fileName?: string;
  recommendedApp?: string;
  openWithLabel?: string;
  openUrl?: string | null;
  protocolUrl?: string | null;
  mode?: "local-launch" | "office-protocol" | "browser";
};

function launchProtocol(protocolUrl: string) {
  const anchor = document.createElement("a");
  anchor.href = protocolUrl;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function launchBrowserOpen(openUrl: string) {
  const opened = window.open(openUrl, "_blank", "noopener,noreferrer");
  if (!opened) {
    const anchor = document.createElement("a");
    anchor.href = openUrl;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }
}

export async function openOriginalInDesktopApp(documentId: string): Promise<DesktopOpenResult> {
  try {
    const body = await api.documentDesktopOpen(documentId);
    if (!body.ok) {
      return {
        ok: false,
        denied: Boolean(body.denied ?? true),
        error: body.error || "Download Originals permission is required.",
      };
    }
    // local-launch: API already opened the file with Windows default app (no download).
    if (body.mode === "local-launch") {
      return body;
    }
    if (body.protocolUrl) {
      launchProtocol(body.protocolUrl);
    } else if (body.openUrl) {
      launchBrowserOpen(body.openUrl);
    }
    return body;
  } catch (err) {
    return {
      ok: false,
      denied: true,
      error: err instanceof Error ? err.message : "Download Originals permission is required.",
    };
  }
}
