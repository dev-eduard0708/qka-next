<template>
  <q-page padding class="qka-workstations">
    <div class="row items-start q-mb-md">
      <div class="col">
        <div class="text-caption text-grey-7">{{ t("workstations.breadcrumb") }}</div>
        <h1 class="text-h5 q-my-none">{{ t("workstations.title") }}</h1>
        <p class="text-body2 text-grey-7 q-mt-sm q-mb-none">{{ t("workstations.body") }}</p>
      </div>
      <div class="col-auto row q-gutter-sm">
        <q-btn color="primary" outline :loading="syncing" :label="t('workstations.sync')" @click="syncInventory" />
        <q-btn color="primary" unelevated :loading="loading" :label="t('workstations.refresh')" @click="reload" />
      </div>
    </div>

    <q-tabs v-model="section" dense class="q-mb-md" active-color="primary" indicator-color="primary" align="left">
      <q-route-tab name="agents" :label="t('workstations.tabAgents')" :to="{ name: 'employee-computers' }" />
      <q-route-tab name="workstations" :label="t('workstations.tabWorkstations')" :to="{ name: 'workstations' }" />
    </q-tabs>

    <q-banner v-if="error" class="bg-negative text-white q-mb-md">{{ error }}</q-banner>
    <q-banner v-if="syncNote" class="bg-grey-2 q-mb-md">{{ syncNote }}</q-banner>

    <div class="row q-col-gutter-md q-mb-md">
      <div class="col-6 col-sm-4 col-md-2" v-for="card in summaryCards" :key="card.label">
        <div class="text-caption text-grey-7">{{ card.label }}</div>
        <div class="text-h6">{{ card.value }}</div>
      </div>
    </div>

    <div class="row q-col-gutter-sm q-mb-md items-center">
      <div class="col-12 col-md-3">
        <q-input v-model="search" dense outlined clearable :label="t('workstations.search')" @keyup.enter="reload" />
      </div>
      <div class="col-12 col-md-3">
        <q-select
          v-model="ou"
          :options="ouOptions"
          dense
          outlined
          clearable
          emit-value
          map-options
          :label="t('workstations.ou')"
        />
      </div>
      <div class="col-6 col-md-2">
        <q-select
          v-model="reachability"
          :options="reachOptions"
          dense
          outlined
          clearable
          emit-value
          map-options
          :label="t('workstations.reachability')"
        />
      </div>
      <div class="col-6 col-md-2">
        <q-select
          v-model="accountStatus"
          :options="accountOptions"
          dense
          outlined
          clearable
          emit-value
          map-options
          :label="t('workstations.accountStatus')"
        />
      </div>
      <div class="col-auto">
        <q-btn color="primary" flat :label="t('workstations.applyFilters')" @click="reload" />
      </div>
      <div class="col-auto">
        <q-btn
          color="primary"
          outline
          :disable="!selectedRows.length"
          :loading="probing"
          :label="t('workstations.bulkPing')"
          @click="bulkPing"
        />
      </div>
      <div class="col-auto">
        <q-btn color="primary" outline disable :label="t('workstations.deployAgent')" />
      </div>
    </div>

    <q-linear-progress v-if="probing" indeterminate color="primary" class="q-mb-sm" />
    <div v-if="probeProgress" class="text-caption text-grey-7 q-mb-sm">{{ probeProgress }}</div>

    <q-table
      flat
      bordered
      dense
      row-key="id"
      :rows="rows"
      :columns="columns"
      :loading="loading"
      selection="multiple"
      v-model:selected="selectedRows"
      v-model:pagination="pagination"
      :rows-number="total"
      @request="onRequest"
    >
      <template #body-cell-accountStatus="props">
        <q-td :props="props">
          <q-chip dense square :color="props.row.accountEnabled ? 'positive' : 'grey-5'" text-color="white" :label="props.value" />
        </q-td>
      </template>
      <template #body-cell-reachabilityStatus="props">
        <q-td :props="props">
          <q-chip dense square :color="reachColor(props.row.reachabilityStatus)" text-color="white" :label="reachLabel(props.row.reachabilityStatus)" />
        </q-td>
      </template>
      <template #body-cell-agentStatusLabel="props">
        <q-td :props="props">
          <q-chip dense square outline :color="agentColor(props.row.agentStatus)" :label="props.value" />
        </q-td>
      </template>
      <template #body-cell-actions="props">
        <q-td :props="props">
          <q-btn dense flat color="primary" :label="t('workstations.ping')" :loading="probingId === props.row.id" @click="pingOne(props.row)" />
          <q-btn dense flat color="primary" :label="t('workstations.assign')" @click="openAssign(props.row)" />
        </q-td>
      </template>
    </q-table>

    <div class="text-caption text-grey-6 q-mt-md">{{ t("workstations.icmpNote") }}</div>
    <div class="text-caption text-grey-6">{{ t("workstations.deployNote") }}</div>

    <q-dialog v-model="assignOpen">
      <q-card style="min-width: 360px">
        <q-card-section class="text-h6">{{ t("workstations.assignTitle") }}</q-card-section>
        <q-card-section>
          <div class="text-caption q-mb-sm">{{ assignTarget?.computerName }}</div>
          <q-select
            v-model="assignUserId"
            :options="employeeOptions"
            dense
            outlined
            emit-value
            map-options
            clearable
            :label="t('workstations.selectEmployee')"
          />
          <div class="text-caption text-grey-7 q-mt-sm">{{ t("workstations.assignHint") }}</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat :label="t('workstations.cancel')" v-close-popup />
          <q-btn color="primary" unelevated :loading="busy" :label="t('workstations.save')" @click="saveAssign" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { api, type DirectoryUser, type Workstation } from "@/api";

const { t } = useI18n();
const section = ref("workstations");
const loading = ref(false);
const syncing = ref(false);
const probing = ref(false);
const probingId = ref<string | null>(null);
const busy = ref(false);
const error = ref("");
const syncNote = ref("");
const probeProgress = ref("");
const rows = ref<Workstation[]>([]);
const selectedRows = ref<Workstation[]>([]);
const total = ref(0);
const summary = ref({ totalPresent: 0, assigned: 0, reachable: 0, noReply: 0, dnsFailed: 0, unchecked: 0 });
const ous = ref<string[]>([]);
const search = ref("");
const ou = ref<string | null>(null);
const reachability = ref<string | null>(null);
const accountStatus = ref<string | null>(null);
const employees = ref<DirectoryUser[]>([]);
const assignOpen = ref(false);
const assignTarget = ref<Workstation | null>(null);
const assignUserId = ref<string | null>(null);
const pagination = ref({ page: 1, rowsPerPage: 25, sortBy: "computerName", descending: false });

const columns = [
  { name: "computerName", label: t("workstations.colComputer"), field: "computerName", align: "left" as const, sortable: true },
  { name: "dnsHostname", label: t("workstations.colDns"), field: "dnsHostname", align: "left" as const, sortable: true },
  { name: "ipAddress", label: t("workstations.colIp"), field: "ipAddress", align: "left" as const, sortable: true },
  { name: "ou", label: t("workstations.colOu"), field: "ou", align: "left" as const, sortable: true },
  { name: "accountStatus", label: t("workstations.colAccount"), field: "accountStatus", align: "left" as const },
  {
    name: "assignedEmployeeName",
    label: t("workstations.colEmployee"),
    field: (r: Workstation) => r.assignedEmployeeName || "—",
    align: "left" as const,
  },
  { name: "reachabilityStatus", label: t("workstations.colReach"), field: "reachabilityStatus", align: "left" as const },
  {
    name: "lastCheckedAt",
    label: t("workstations.colLastChecked"),
    field: (r: Workstation) => r.lastCheckedAt || "—",
    align: "left" as const,
    sortable: true,
  },
  { name: "agentStatusLabel", label: t("workstations.colAgent"), field: "agentStatusLabel", align: "left" as const },
  { name: "agentVersion", label: t("workstations.colAgentVersion"), field: (r: Workstation) => r.agentVersion || "—", align: "left" as const },
  {
    name: "lastHeartbeatAt",
    label: t("workstations.colHeartbeat"),
    field: (r: Workstation) => r.lastHeartbeatAt || "—",
    align: "left" as const,
  },
  {
    name: "indexingStatus",
    label: t("workstations.colIndexing"),
    field: (r: Workstation) => r.indexingStatus,
    align: "left" as const,
  },
  { name: "actions", label: t("workstations.colActions"), field: "id", align: "right" as const },
];

const ouOptions = computed(() => ous.value.map((v) => ({ label: v, value: v })));
const reachOptions = [
  { label: t("workstations.reach.unchecked"), value: "unchecked" },
  { label: t("workstations.reach.reachable"), value: "reachable" },
  { label: t("workstations.reach.no_reply"), value: "no_reply" },
  { label: t("workstations.reach.dns_failed"), value: "dns_failed" },
  { label: t("workstations.reach.probe_error"), value: "probe_error" },
];
const accountOptions = [
  { label: t("workstations.account.enabled"), value: "enabled" },
  { label: t("workstations.account.disabled"), value: "disabled" },
];
const employeeOptions = computed(() =>
  employees.value
    .filter((e) => e.qkaUserId && (e.linkedIdentities || "").includes("ad"))
    .map((e) => ({ label: `${e.displayName} (${e.primaryEmail || e.qkaUserId})`, value: e.qkaUserId as string })),
);
const summaryCards = computed(() => [
  { label: t("workstations.statPresent"), value: summary.value.totalPresent },
  { label: t("workstations.statAssigned"), value: summary.value.assigned },
  { label: t("workstations.statReachable"), value: summary.value.reachable },
  { label: t("workstations.statNoReply"), value: summary.value.noReply },
  { label: t("workstations.statDnsFailed"), value: summary.value.dnsFailed },
  { label: t("workstations.statUnchecked"), value: summary.value.unchecked },
]);

function reachColor(status: string) {
  if (status === "reachable") return "positive";
  if (status === "dns_failed" || status === "probe_error") return "negative";
  if (status === "no_reply") return "warning";
  return "grey";
}
function reachLabel(status: string) {
  return t(`workstations.reach.${status}`);
}
function agentColor(status: string) {
  if (status === "connected") return "positive";
  if (status === "registered_offline") return "warning";
  return "grey";
}

async function reload() {
  loading.value = true;
  error.value = "";
  try {
    const page = await api.workstations({
      search: search.value || undefined,
      ou: ou.value || undefined,
      reachability: reachability.value || undefined,
      accountStatus: accountStatus.value || undefined,
      sort: pagination.value.sortBy || "computerName",
      sortDir: pagination.value.descending ? "desc" : "asc",
      page: pagination.value.page,
      pageSize: pagination.value.rowsPerPage,
    });
    rows.value = page.items;
    total.value = page.total;
    summary.value = page.summary;
    ous.value = page.ous;
    const keep = new Set(selectedRows.value.map((r) => r.id));
    selectedRows.value = rows.value.filter((r) => keep.has(r.id));
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("workstations.loadError");
  } finally {
    loading.value = false;
  }
}

async function syncInventory() {
  syncing.value = true;
  error.value = "";
  syncNote.value = "";
  try {
    const result = await api.syncWorkstations({ includeServers: false });
    syncNote.value = t("workstations.syncDone", {
      discovered: result.discovered,
      created: result.created,
      updated: result.updated,
      missing: result.missingMarked,
    });
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("workstations.syncError");
  } finally {
    syncing.value = false;
  }
}

async function pingOne(row: Workstation) {
  probingId.value = row.id;
  error.value = "";
  try {
    await api.pingWorkstation(row.id, 2000);
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("workstations.pingError");
  } finally {
    probingId.value = null;
  }
}

async function bulkPing() {
  if (!selectedRows.value.length) return;
  probing.value = true;
  probeProgress.value = t("workstations.probeProgress", { n: selectedRows.value.length });
  error.value = "";
  try {
    const result = await api.pingWorkstationsBulk(
      selectedRows.value.map((r) => r.id),
      { timeoutMs: 2000, concurrency: 8 },
    );
    probeProgress.value = t("workstations.probeDone", { n: result.processed });
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("workstations.pingError");
    probeProgress.value = "";
  } finally {
    probing.value = false;
  }
}

function openAssign(row: Workstation) {
  assignTarget.value = row;
  assignUserId.value = row.assignedEmployeeId;
  assignOpen.value = true;
}

async function saveAssign() {
  if (!assignTarget.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api.assignWorkstationEmployee(assignTarget.value.id, assignUserId.value);
    assignOpen.value = false;
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("workstations.assignError");
  } finally {
    busy.value = false;
  }
}

function onRequest(props: { pagination: typeof pagination.value }) {
  pagination.value = props.pagination;
  void reload();
}

watch([ou, reachability, accountStatus], () => {
  pagination.value.page = 1;
});

onMounted(async () => {
  try {
    const userPage = await api.listDirectoryUsers({ pageSize: 200 });
    employees.value = userPage.items;
  } catch {
    employees.value = [];
  }
  await reload();
});
</script>
