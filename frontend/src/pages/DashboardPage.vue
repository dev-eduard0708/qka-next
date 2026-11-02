<template>
  <q-page padding>
    <h1 class="text-h5 q-mb-md">{{ t("dashboard.title") }}</h1>
    <p class="text-body2 q-mb-lg">{{ t("dashboard.note") }}</p>
    <div class="row q-col-gutter-md">
      <div class="col-12 col-sm-6 col-md-3">
        <q-card bordered>
          <q-card-section>
            <div class="text-caption">{{ t("dashboard.googleUsers") }}</div>
            <div class="text-h5">{{ summary?.googleWorkspaceUsers ?? "—" }}</div>
            <div class="text-caption q-mt-xs">
              {{ t("dashboard.allowlistedEmails") }}: {{ summary?.allowlistedEmails ?? "—" }}
            </div>
          </q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-sm-6 col-md-3">
        <q-card bordered>
          <q-card-section>
            <div class="text-caption">{{ t("dashboard.sources") }}</div>
            <div class="text-h5">{{ summary?.sourceCount ?? "—" }}</div>
            <div class="text-caption q-mt-xs">
              {{ t("dashboard.synologyConnections") }}: {{ summary?.synologyConnections ?? "—" }}
            </div>
          </q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-sm-6 col-md-3">
        <q-card bordered>
          <q-card-section>
            <div class="text-caption">{{ t("dashboard.inventory") }}</div>
            <div class="text-h5">{{ summary?.inventoryCount ?? "—" }}</div>
          </q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-sm-6 col-md-3">
        <q-card bordered>
          <q-card-section>
            <div class="text-caption">{{ t("dashboard.extracted") }}</div>
            <div class="text-h5">{{ summary?.extractedCount ?? "—" }}</div>
          </q-card-section>
        </q-card>
      </div>
    </div>

    <q-card bordered class="q-mt-lg">
      <q-card-section>
        <div class="text-subtitle1 q-mb-sm">{{ t("dashboard.sourceBreakdown") }}</div>
        <q-list v-if="summary?.sources?.length" bordered separator>
          <q-item
            v-for="source in summary.sources"
            :key="source.id"
            clickable
            v-ripple
            :to="source.kind === 'synology' ? '/integrations/synology' : undefined"
          >
            <q-item-section avatar>
              <q-icon :name="source.kind === 'synology' ? 'storage' : 'hub'" />
            </q-item-section>
            <q-item-section>
              <q-item-label>{{ source.name }}</q-item-label>
              <q-item-label caption>
                {{ source.connectionCount }}
                {{ source.connectionCount === 1 ? t("dashboard.connectionOne") : t("dashboard.connectionMany") }}
              </q-item-label>
            </q-item-section>
          </q-item>
        </q-list>
        <div v-else class="text-body2">{{ t("dashboard.noSources") }}</div>
      </q-card-section>
    </q-card>

    <div class="row items-center q-mt-xl q-mb-sm">
      <div class="col">
        <div class="text-h6 q-my-none">{{ t("dashboard.systemIntegrations") }}</div>
        <div class="text-caption">{{ t("dashboard.systemIntegrationsHint") }}</div>
      </div>
      <div class="col-auto row q-gutter-sm">
        <q-btn
          v-if="session.user?.superAdmin"
          outline
          color="primary"
          :loading="testing"
          :label="t('dashboard.testMcp')"
          @click="runTest"
        />
        <q-btn flat color="primary" :loading="loadingStatus" :label="t('dashboard.refreshStatus')" @click="loadStatus" />
      </div>
    </div>

    <div class="row q-col-gutter-md">
      <div v-for="card in statusCards" :key="card.key" class="col-12 col-sm-6 col-md-4">
        <q-card bordered>
          <q-card-section>
            <div class="row items-center no-wrap">
              <div class="col">
                <div class="text-caption">{{ card.title }}</div>
                <div class="text-subtitle1">{{ statusLabel(card.status) }}</div>
              </div>
              <q-chip dense :color="statusColor(card.status)" text-color="white" :label="statusLabel(card.status)" />
            </div>
            <div v-if="card.detail" class="text-caption q-mt-sm">{{ card.detail }}</div>
            <div v-if="card.error" class="text-negative text-caption q-mt-xs">{{ card.error }}</div>
            <div class="text-caption q-mt-sm">{{ t("dashboard.lastCheck") }}: {{ card.lastCheck || "—" }}</div>
          </q-card-section>
        </q-card>
      </div>
    </div>

    <q-banner v-if="testMessage" :class="testOk ? 'bg-positive text-white' : 'bg-warning text-dark'" class="q-mt-md">
      {{ testMessage }}
    </q-banner>
    <q-banner v-if="error" class="bg-negative text-white q-mt-md">{{ error }}</q-banner>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { api, type IntegrationStatus } from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const summary = ref<{
  inventoryCount: number;
  extractedCount: number;
  googleWorkspaceUsers: number;
  allowlistedEmails: number;
  synologyConnections: number;
  sourceCount: number;
  sources: Array<{ id: string; name: string; kind: string; connectionCount: number }>;
} | null>(null);
const integrations = ref<IntegrationStatus | null>(null);
const error = ref("");
const loadingStatus = ref(false);
const testing = ref(false);
const testMessage = ref("");
const testOk = ref(false);
let pollTimer: number | null = null;

const statusCards = computed(() => {
  const s = integrations.value;
  if (!s) {
    return [
      { key: "fastapi", title: t("dashboard.cardFastApi"), status: "not_verified", detail: "", error: null, lastCheck: null },
    ];
  }
  return [
    { key: "fastapi", title: t("dashboard.cardFastApi"), status: s.fastapi.status, detail: "", error: s.fastapi.error, lastCheck: s.fastapi.lastCheck },
    { key: "postgresql", title: t("dashboard.cardPostgres"), status: s.postgresql.status, detail: "", error: s.postgresql.error, lastCheck: s.postgresql.lastCheck },
    {
      key: "synology",
      title: t("dashboard.cardSynology"),
      status: s.synologyNas.status,
      detail: `${t("dashboard.synologyConnections")}: ${s.synologyNas.connectionCount ?? "—"}`,
      error: s.synologyNas.error,
      lastCheck: s.synologyNas.lastCheck,
    },
    {
      key: "sync",
      title: t("dashboard.cardSynologySync"),
      status: s.synologySync.status,
      detail: s.synologySync.lastSuccessfulSyncAt ? `${t("dashboard.lastSync")}: ${s.synologySync.lastSuccessfulSyncAt}` : "",
      error: s.synologySync.error,
      lastCheck: s.synologySync.lastCheck,
    },
    {
      key: "index",
      title: t("dashboard.cardDocumentIndex"),
      status: s.documentIndex.status,
      detail: `${t("dashboard.inventory")}: ${s.documentIndex.inventoryCount ?? "—"} · ${t("dashboard.extracted")}: ${s.documentIndex.extractedCount ?? "—"}`,
      error: s.documentIndex.error,
      lastCheck: s.documentIndex.lastCheck,
    },
    {
      key: "google",
      title: t("dashboard.cardGoogleAuth"),
      status: s.googleWorkspaceAuth.status,
      detail: `${t("dashboard.allowlistedEmails")}: ${s.googleWorkspaceAuth.allowlistedEmails ?? "—"}`,
      error: s.googleWorkspaceAuth.error,
      lastCheck: s.googleWorkspaceAuth.lastCheck,
    },
    {
      key: "openai",
      title: t("dashboard.cardOpenAi"),
      status: s.openaiApi?.status || "not_configured",
      detail: [
        s.openaiApi?.model ? `${t("dashboard.openai")}: ${s.openaiApi.model}` : t("dashboard.notConnected"),
        s.openaiApi?.lastSuccessfulRequestAt
          ? `${t("dashboard.openaiLastSuccess")}: ${s.openaiApi.lastSuccessfulRequestAt}`
          : null,
        s.openaiApi?.lastError
          ? `${t("dashboard.openaiLastError")}: ${s.openaiApi.lastError}`
          : null,
      ]
        .filter(Boolean)
        .join(" · "),
      error: s.openaiApi?.error ?? null,
      lastCheck: s.openaiApi?.lastCheck ?? null,
    },
    {
      key: "mcp",
      title: t("dashboard.cardMcpServer"),
      status: s.mcpServer.authReady ? s.mcpServer.status : "not_configured",
      detail: `${s.mcpServer.endpoint || ""} · ${(s.mcpServer.tools || []).join(", ")}`,
      error: s.mcpServer.error,
      lastCheck: s.mcpServer.lastCheck,
    },
    {
      key: "tools",
      title: t("dashboard.cardMcpTools"),
      status: s.mcpTools.status,
      detail: `${t("dashboard.mcpSuccess")}: ${s.mcpTools.successCount ?? 0} · ${t("dashboard.mcpFailed")}: ${s.mcpTools.failureCount ?? 0}${s.mcpTools.lastAuthenticatedAt ? ` · ${s.mcpTools.lastAuthenticatedAt}` : ""}`,
      error: null,
      lastCheck: s.mcpTools.lastCheck,
    },
    {
      key: "tunnel",
      title: t("dashboard.cardTunnel"),
      status: s.secureMcpTunnel.status,
      detail: "",
      error: s.secureMcpTunnel.error,
      lastCheck: s.secureMcpTunnel.lastCheck,
    },
    {
      key: "chatgpt",
      title: t("dashboard.cardChatgpt"),
      status: s.chatgptBusiness.status === "live_verified" ? "live_verified" : "not_verified",
      detail: `${s.chatgptBusiness.pluginName || ""} (${s.chatgptBusiness.environment || "staging"})`,
      error: s.chatgptBusiness.error,
      lastCheck: s.chatgptBusiness.lastCheck,
    },
  ];
});

function statusColor(status: string) {
  if (status === "connected" || status === "live_verified") return "positive";
  if (status === "error") return "negative";
  if (status === "disconnected") return "warning";
  return "grey";
}
function statusLabel(status: string) {
  const key = `dashboard.status_${status}` as const;
  const translated = t(key);
  return translated === key ? status : translated;
}

async function loadSummary() {
  if (!session.user) {
    summary.value = null;
    return;
  }
  summary.value = await api.summary();
}

async function loadStatus() {
  if (!session.user?.superAdmin) {
    integrations.value = null;
    return;
  }
  loadingStatus.value = true;
  try {
    integrations.value = await api.chatgptStatus();
    error.value = "";
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("dashboard.statusError");
  } finally {
    loadingStatus.value = false;
  }
}

async function runTest() {
  testing.value = true;
  testMessage.value = "";
  try {
    const result = await api.chatgptTest();
    testOk.value = result.overallOk;
    testMessage.value = result.overallOk
      ? t("dashboard.testOk")
      : `${t("dashboard.testFail")}: ${result.steps.filter((s) => !s.ok).map((s) => s.name).join(", ")}`;
    await loadStatus();
  } catch (err) {
    testOk.value = false;
    testMessage.value = err instanceof Error ? err.message : t("dashboard.testFail");
  } finally {
    testing.value = false;
  }
}

async function load() {
  if (!session.user) {
    summary.value = null;
    integrations.value = null;
    return;
  }
  try {
    await loadSummary();
    await loadStatus();
    error.value = "";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to load summary.";
  }
}

function startPoll() {
  if (pollTimer != null) return;
  pollTimer = window.setInterval(() => {
    if (session.user?.superAdmin) void loadStatus();
  }, 30000);
}
function stopPoll() {
  if (pollTimer != null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

onMounted(() => {
  void load();
  startPoll();
});
onUnmounted(stopPoll);
watch(() => session.user, load);
</script>
