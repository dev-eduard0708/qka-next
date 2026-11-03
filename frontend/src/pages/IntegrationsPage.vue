<template>
  <q-page padding class="qka-integrations">
    <div class="row items-start q-mb-md">
      <div class="col">
        <h1 class="text-h5 q-my-none">{{ t("integrations.title") }}</h1>
        <p class="text-body2 text-grey-7 q-mt-sm q-mb-none">{{ t("integrations.body") }}</p>
      </div>
    </div>

    <q-banner v-if="error" class="bg-negative text-white q-mb-md">{{ error }}</q-banner>

    <div class="row q-col-gutter-md">
      <div v-for="card in cards" :key="card.key" class="col-12 col-md-6">
        <q-card bordered class="qka-integration-card full-height">
          <q-card-section>
            <div class="row items-center q-gutter-sm q-mb-sm">
              <q-icon :name="card.icon" size="28px" color="primary" />
              <div class="text-subtitle1 text-weight-medium">{{ card.title }}</div>
            </div>
            <p class="text-body2 text-grey-8 q-mb-md">{{ card.description }}</p>
            <div class="row items-center q-gutter-xs q-mb-sm">
              <q-icon
                :name="statusIcon(card.status)"
                size="18px"
                :color="statusColor(card.status)"
              />
              <span class="text-body2">{{ statusLabel(card.status) }}</span>
            </div>
            <div v-if="card.lastCheck" class="text-caption text-grey-7">
              {{ t("integrations.lastCheck") }}: {{ formatDate(card.lastCheck) }}
            </div>
            <div v-else-if="card.note" class="text-caption text-grey-7">{{ card.note }}</div>
          </q-card-section>
          <q-card-actions align="left" class="q-px-md q-pb-md">
            <q-btn
              flat
              dense
              color="primary"
              class="text-weight-bold"
              :to="card.to"
              :label="card.action"
            />
          </q-card-actions>
        </q-card>
      </div>
    </div>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const error = ref("");
const hub = ref<{
  synology?: { status: string; lastSuccessfulCheck?: string | null };
  activeDirectory?: { status: string; lastSuccessfulCheck?: string | null };
  googleWorkspace?: { status: string; note?: string };
  chatgptMcp?: { status: string; note?: string };
} | null>(null);

const cards = computed(() => [
  {
    key: "synology",
    icon: "dns",
    title: t("synology.title"),
    description: t("integrations.synologyHint"),
    status: hub.value?.synology?.status || "not_configured",
    lastCheck: hub.value?.synology?.lastSuccessfulCheck || null,
    note: "",
    to: "/integrations/synology",
    action: t("integrations.manageConnection"),
  },
  {
    key: "ad",
    icon: "account_tree",
    title: t("ad.title"),
    description: t("integrations.adHint"),
    status: hub.value?.activeDirectory?.status || "not_configured",
    lastCheck: hub.value?.activeDirectory?.lastSuccessfulCheck || null,
    note: "",
    to: "/integrations/active-directory",
    action:
      (hub.value?.activeDirectory?.status || "not_configured") === "connected"
        ? t("integrations.manageConnection")
        : t("integrations.configureDomain"),
  },
  {
    key: "google",
    icon: "groups",
    title: t("integrations.googleTitle"),
    description: t("integrations.googleHint"),
    status: hub.value?.googleWorkspace?.status || "configured",
    lastCheck: null,
    note: hub.value?.googleWorkspace?.note || t("integrations.googleHint"),
    to: "/access",
    action: t("integrations.manageSettings"),
  },
  {
    key: "mcp",
    icon: "power",
    title: t("integrations.mcpTitle"),
    description: t("integrations.mcpHint"),
    status: hub.value?.chatgptMcp?.status || "configured",
    lastCheck: null,
    note: hub.value?.chatgptMcp?.note || "",
    to: "/",
    action: t("integrations.manageSettings"),
  },
]);

function statusIcon(status: string) {
  if (status === "connected") return "check_circle";
  if (status === "failed") return "error";
  if (status === "disabled") return "block";
  return "radio_button_unchecked";
}

function statusColor(status: string) {
  if (status === "connected") return "positive";
  if (status === "failed") return "negative";
  return "grey-6";
}

function statusLabel(status: string) {
  if (status === "connected") return t("integrations.statusConnected");
  if (status === "failed") return t("integrations.statusFailed");
  if (status === "disabled") return t("integrations.statusDisabled");
  if (status === "configured") return t("integrations.statusConfigured");
  return t("integrations.statusNotConfigured");
}

function formatDate(value: string) {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

onMounted(async () => {
  if (!session.user?.superAdmin) return;
  try {
    hub.value = await api.adHubStatus();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("integrations.loadError");
  }
});
</script>

<style scoped>
.qka-integration-card {
  border-radius: 12px;
}
</style>
