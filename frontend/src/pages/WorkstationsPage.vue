<template>
  <q-page padding class="qka-workstations">
    <div class="row items-start q-mb-md">
      <div class="col">
        <div class="text-caption text-grey-7">{{ t("workstations.breadcrumb") }}</div>
        <h1 class="text-h5 q-my-none">{{ t("workstations.title") }}</h1>
        <p class="text-body2 text-grey-7 q-mt-sm q-mb-none">{{ t("workstations.body") }}</p>
      </div>
      <div class="col-auto row q-gutter-sm">
        <q-btn color="primary" outline :loading="syncing" :label="t('workstations.sync')" @click="openSyncDialog" />
        <q-btn color="primary" unelevated :loading="loading" :label="t('workstations.refresh')" @click="() => reload()" />
      </div>
    </div>

    <q-tabs v-model="section" dense class="q-mb-md" active-color="primary" indicator-color="primary" align="left">
      <q-route-tab name="agents" :label="t('workstations.tabAgents')" :to="{ name: 'employee-computers' }" />
      <q-route-tab name="workstations" :label="t('workstations.tabWorkstations')" :to="{ name: 'workstations' }" />
    </q-tabs>

    <q-banner v-if="error" class="bg-negative text-white q-mb-md">{{ error }}</q-banner>
    <q-banner v-if="syncNote" class="bg-grey-2 q-mb-md">{{ syncNote }}</q-banner>
    <q-banner v-if="deployPrereqBanner" class="bg-warning text-dark q-mb-md">
      <div class="text-subtitle2">{{ t("workstations.deployPrereqTitle") }}</div>
      <ul class="q-my-sm q-pl-md">
        <li v-for="(b, i) in deployPrereqs?.blockers || []" :key="i">{{ b }}</li>
      </ul>
      <div class="text-caption">{{ deployPrereqs?.note }}</div>
    </q-banner>
    <q-banner v-else-if="deployPrereqs?.deployEnabled" class="bg-green-1 q-mb-md">
      {{ t("workstations.deployReadyNote", { endpoint: deployPrereqs.httpsEndpoint, group: deployPrereqs.gpoTargetGroup }) }}
    </q-banner>
    <q-banner v-if="scopeBanner" class="bg-blue-1 q-mb-md">
      <div class="row items-center">
        <div class="col">{{ scopeBanner }}</div>
        <div class="col-auto">
          <q-toggle v-model="scopeOnly" dense :label="t('workstations.scopeOnlyToggle')" @update:model-value="onScopeToggle" />
        </div>
      </div>
    </q-banner>

    <div class="row q-col-gutter-md q-mb-md">
      <div class="col-6 col-sm-4 col-md-2" v-for="card in summaryCards" :key="card.label">
        <div class="text-caption text-grey-7">{{ card.label }}</div>
        <div class="text-h6">{{ loadFailed ? "—" : card.value }}</div>
      </div>
    </div>

    <div class="row q-col-gutter-sm q-mb-md items-center">
      <div class="col-12 col-md-3">
        <q-input v-model="search" dense outlined clearable :label="t('workstations.search')" @keyup.enter="() => reload({ page: 1 })" />
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
        <q-btn color="primary" flat :label="t('workstations.applyFilters')" @click="() => reload({ page: 1 })" />
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
        <q-btn
          color="primary"
          outline
          :disable="!canDeploySelected"
          :loading="deploying"
          :label="t('workstations.deployAgent')"
          @click="openDeploy"
        >
          <q-tooltip v-if="!canDeploySelected">{{ deployDisabledReason }}</q-tooltip>
        </q-btn>
      </div>
    </div>

    <q-linear-progress v-if="probing" indeterminate color="primary" class="q-mb-sm" />
    <div v-if="probeProgress" class="text-caption text-grey-7 q-mb-sm">{{ probeProgress }}</div>

    <q-banner v-if="loadFailed && !loading" class="bg-grey-2 q-mb-md">
      {{ t("workstations.loadFailedHint") }}
    </q-banner>

    <div class="text-caption text-grey-7 q-mb-sm" v-if="!loadFailed">
      {{ t("workstations.tableCount", { shown: rows.length, total }) }}
    </div>

    <q-table
      flat
      bordered
      dense
      row-key="id"
      :rows="loadFailed ? [] : rows"
      :columns="columns"
      :loading="loading"
      selection="multiple"
      v-model:selected="selectedRows"
      v-model:pagination="pagination"
      :rows-per-page-options="[25, 50, 100, 200]"
      binary-state-sort
      :no-data-label="loadFailed ? t('workstations.noDataBecauseError') : t('workstations.noData')"
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
      <template #body-cell-deployStatus="props">
        <q-td :props="props">
          <div>{{ props.value }}</div>
          <div v-if="props.row.deploymentJob?.lastError" class="text-caption text-negative">
            {{ props.row.deploymentJob.lastError }}
          </div>
          <div v-else-if="props.row.deployAgent?.note && !props.row.deployAgent.enabled" class="text-caption text-grey-7">
            {{ props.row.deployAgent.note }}
          </div>
        </q-td>
      </template>
      <template #body-cell-actions="props">
        <q-td :props="props">
          <q-btn dense flat color="primary" :label="t('workstations.ping')" :loading="probingId === props.row.id" @click="pingOne(props.row)" />
          <q-btn dense flat color="primary" :label="t('workstations.assign')" @click="openAssign(props.row)" />
          <q-btn
            v-if="canRetryJob(props.row)"
            dense
            flat
            color="primary"
            :label="t('workstations.retryDeploy')"
            :loading="jobBusyId === props.row.deploymentJob?.id"
            @click="retryDeploy(props.row)"
          />
          <q-btn
            v-if="canCancelJob(props.row)"
            dense
            flat
            color="negative"
            :label="t('workstations.cancelDeploy')"
            :loading="jobBusyId === props.row.deploymentJob?.id"
            @click="cancelDeploy(props.row)"
          />
          <q-btn
            v-if="props.row.desktopDeviceId"
            dense
            flat
            color="primary"
            :label="t('workstations.openAgent')"
            :to="{ name: 'employee-computers', query: { deviceId: props.row.desktopDeviceId } }"
          />
        </q-td>
      </template>
    </q-table>

    <div class="text-caption text-grey-6 q-mt-md">{{ t("workstations.icmpNote") }}</div>
    <div class="text-caption text-grey-6">{{ t("workstations.deployNote") }}</div>

    <q-dialog v-model="syncOpen" persistent>
      <q-card style="min-width: 560px; max-width: 760px">
        <q-card-section class="text-h6">{{ t("workstations.syncTitle") }}</q-card-section>
        <q-card-section>
          <div class="text-body2 text-grey-8 q-mb-sm">{{ t("workstations.syncHint") }}</div>
          <div class="text-caption text-warning q-mb-md">{{ t("workstations.syncSubtreeNote") }}</div>
          <div class="text-caption text-grey-7 q-mb-sm" v-if="syncConnectionName">
            {{ syncConnectionName }} · {{ syncBaseDn }}
          </div>
          <div class="row items-center q-gutter-xs q-mb-sm">
            <q-btn
              flat
              dense
              color="primary"
              icon="arrow_upward"
              :disable="!canOuGoUp"
              :label="t('workstations.ouUp')"
              @click="goOuUp"
            />
            <span class="text-caption text-grey-8">{{ syncParentDn || syncBaseDn }}</span>
          </div>
          <div class="row q-col-gutter-sm q-mb-md items-center">
            <div class="col">
              <q-input v-model="syncOuSearch" dense outlined clearable :label="t('workstations.searchOus')" @keyup.enter="loadSyncOus()" />
            </div>
            <div class="col-auto">
              <q-btn outline color="primary" :loading="syncOuLoading" :label="t('workstations.loadOus')" @click="loadSyncOus()" />
            </div>
          </div>
          <div class="row q-mb-sm">
            <q-btn flat dense color="primary" :label="t('workstations.selectAllOus')" @click="setAllVisibleOus(true)" />
            <q-btn flat dense color="primary" :label="t('workstations.clearOus')" @click="clearSelectedOus" />
          </div>
          <q-list bordered separator style="max-height: 280px; overflow: auto">
            <q-item v-for="item in syncOuItems" :key="item.distinguishedName">
              <q-item-section avatar>
                <q-checkbox
                  :model-value="!!selectedOuMap[item.distinguishedName]"
                  @update:model-value="(v: boolean) => toggleOu(item, v)"
                />
              </q-item-section>
              <q-item-section>
                <q-item-label>{{ item.name }}</q-item-label>
                <q-item-label caption>{{ item.distinguishedName }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn flat dense color="primary" :label="t('workstations.openOu')" @click="openOuChildren(item.distinguishedName)" />
              </q-item-section>
            </q-item>
            <q-item v-if="!syncOuItems.length">
              <q-item-section class="text-grey-7">{{ t("workstations.noOus") }}</q-item-section>
            </q-item>
          </q-list>
          <div class="text-subtitle2 q-mt-md q-mb-xs">{{ t("workstations.selectedForSync") }}</div>
          <div class="q-gutter-xs" style="max-height: 120px; overflow: auto">
            <q-chip
              v-for="dn in Object.keys(selectedOuMap)"
              :key="dn"
              removable
              dense
              color="primary"
              text-color="white"
              :label="selectedOuMap[dn].name"
              @remove="removeSelectedOu(dn)"
            />
            <div v-if="selectedSyncOuCount < 1" class="text-caption text-grey-7">{{ t("workstations.noneSelected") }}</div>
          </div>
          <div class="text-caption q-mt-sm">
            {{ t("workstations.selectedOuCount", { n: selectedSyncOuCount }) }}
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat :label="t('workstations.cancel')" v-close-popup />
          <q-btn
            color="primary"
            unelevated
            :loading="syncing"
            :disable="selectedSyncOuCount < 1"
            :label="t('workstations.syncConfirm')"
            @click="confirmSyncInventory"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>

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

    <q-dialog v-model="deployOpen" persistent>
      <q-card style="min-width: 480px; max-width: 640px">
        <q-card-section class="text-h6">{{ t("workstations.deployTitle") }}</q-card-section>
        <q-card-section>
          <div class="text-body2 q-mb-sm">{{ t("workstations.deployConfirmHint", { n: selectedRows.length }) }}</div>
          <ul class="q-pl-md">
            <li v-for="row in selectedRows" :key="row.id">
              {{ row.computerName }}
              <span class="text-grey-7">— {{ row.assignedEmployeeName || t("workstations.unassigned") }}</span>
            </li>
          </ul>
          <div class="text-caption text-warning q-mb-md">{{ t("workstations.deployScopeWarning") }}</div>
          <q-input
            v-model="deployConfirmation"
            dense
            outlined
            :label="t('workstations.deployConfirmLabel', { phrase: deployPrereqs?.confirmationPhrase || 'DEPLOY SELECTED' })"
          />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat :label="t('workstations.cancel')" v-close-popup />
          <q-btn
            color="primary"
            unelevated
            :loading="deploying"
            :disable="deployConfirmation.trim() !== (deployPrereqs?.confirmationPhrase || '')"
            :label="t('workstations.deployConfirm')"
            @click="confirmDeploy"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <q-dialog v-model="tokenOpen" persistent>
      <q-card style="min-width: 520px; max-width: 720px">
        <q-card-section class="text-h6">{{ t("workstations.tokenTitle") }}</q-card-section>
        <q-card-section>
          <div class="text-body2 q-mb-sm">{{ t("workstations.tokenHint") }}</div>
          <div class="text-caption text-grey-7 q-mb-md">{{ tokenInstruction }}</div>
          <div v-for="job in tokenJobs" :key="job.id" class="q-mb-md">
            <div class="text-subtitle2">{{ job.computerName }}</div>
            <code class="text-body2" style="word-break: break-all">{{ job.machineEnrollmentToken }}</code>
          </div>
          <div class="text-caption text-negative">{{ t("workstations.tokenNeverSysvol") }}</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn color="primary" unelevated :label="t('workstations.tokenDone')" @click="closeTokenDialog" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { api, ApiError, type DeployPrerequisites, type DirectoryUser, type Workstation } from "@/api";

const { t } = useI18n();
const section = ref("workstations");
const loading = ref(false);
const syncing = ref(false);
const probing = ref(false);
const probingId = ref<string | null>(null);
const busy = ref(false);
const deploying = ref(false);
const jobBusyId = ref<string | null>(null);
const error = ref("");
const loadFailed = ref(false);
const syncNote = ref("");
const probeProgress = ref("");
const rows = ref<Workstation[]>([]);
const selectedRows = ref<Workstation[]>([]);
const deployPrereqs = ref<DeployPrerequisites | null>(null);
const deployOpen = ref(false);
const deployConfirmation = ref("");
const tokenOpen = ref(false);
const tokenInstruction = ref("");
const tokenJobs = ref<Array<{ id: string; computerName: string; machineEnrollmentToken: string }>>([]);
const total = ref(0);
const summary = ref({ totalPresent: 0, assigned: 0, reachable: 0, noReply: 0, dnsFailed: 0, unchecked: 0 });
const ous = ref<string[]>([]);
const activeSyncOus = ref<Array<{ name: string; distinguishedName: string }>>([]);
const scopeOnly = ref(true);
const search = ref("");
const ou = ref<string | null>(null);
const reachability = ref<string | null>(null);
const accountStatus = ref<string | null>(null);
const employees = ref<DirectoryUser[]>([]);
const assignOpen = ref(false);
const assignTarget = ref<Workstation | null>(null);
const assignUserId = ref<string | null>(null);
const syncOpen = ref(false);
const syncOuLoading = ref(false);
const syncOuSearch = ref("");
const syncConnectionId = ref<string | null>(null);
const syncConnectionName = ref("");
const syncBaseDn = ref("");
const syncParentDn = ref("");
const syncOuItems = ref<Array<{ name: string; distinguishedName: string; selected: boolean }>>([]);
const selectedOuMap = ref<Record<string, { name: string; distinguishedName: string }>>({});
const pagination = ref({
  page: 1,
  rowsPerPage: 25,
  rowsNumber: 0,
  sortBy: "computerName",
  descending: false,
});
const selectedSyncOuCount = computed(() => Object.keys(selectedOuMap.value).length);
const canOuGoUp = computed(() => {
  const parent = (syncParentDn.value || "").toLowerCase();
  const base = (syncBaseDn.value || "").toLowerCase();
  return Boolean(parent && base && parent !== base);
});

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
  {
    name: "deployStatus",
    label: t("workstations.colDeployStatus"),
    field: (r: Workstation) => r.deploymentJob?.status || r.deployAgent?.status || "—",
    align: "left" as const,
  },
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
    .filter((e) => e.qkaUserId && (e.adLinked || e.linkedIdentities === "ad" || e.linkedIdentities === "both"))
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
const scopeBanner = computed(() => {
  if (!scopeOnly.value || !activeSyncOus.value.length) return "";
  const names = activeSyncOus.value.map((o) => o.name).join(", ");
  return t("workstations.scopeBanner", { ous: names });
});
const deployPrereqBanner = computed(
  () => Boolean(deployPrereqs.value && !deployPrereqs.value.deployEnabled && deployPrereqs.value.blockers?.length),
);
const canDeploySelected = computed(() => {
  if (!deployPrereqs.value?.deployEnabled) return false;
  if (!selectedRows.value.length) return false;
  if (selectedRows.value.length > (deployPrereqs.value.maxBulk || 50)) return false;
  return selectedRows.value.every((r) => r.deployAgent?.enabled);
});
const deployDisabledReason = computed(() => {
  if (!deployPrereqs.value?.deployEnabled) {
    return (deployPrereqs.value?.blockers || []).join(" ") || t("workstations.deployNote");
  }
  if (!selectedRows.value.length) return t("workstations.deploySelectHint");
  const blocked = selectedRows.value.filter((r) => !r.deployAgent?.enabled);
  if (blocked.length) {
    return blocked.map((r) => `${r.computerName}: ${r.deployAgent?.note || ""}`).join(" ");
  }
  return "";
});

function canRetryJob(row: Workstation) {
  const st = row.deploymentJob?.status;
  return st === "failed" || st === "waiting_for_computer" || st === "cancelled";
}
function canCancelJob(row: Workstation) {
  const st = row.deploymentJob?.status;
  return st === "requested" || st === "approved" || st === "scheduled" || st === "waiting_for_computer" || st === "installing";
}

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

function formatApiFailure(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const detail = err.message?.trim() || fallback;
    return t("workstations.apiFailure", { status: err.status, detail });
  }
  return err instanceof Error ? err.message : fallback;
}

async function reload(opts?: {
  page?: number;
  rowsPerPage?: number;
  sortBy?: string;
  descending?: boolean;
}) {
  const pageNum = opts?.page ?? pagination.value.page;
  const rowsPerPage = opts?.rowsPerPage ?? pagination.value.rowsPerPage;
  const sortBy = opts?.sortBy ?? pagination.value.sortBy ?? "computerName";
  const descending = opts?.descending ?? pagination.value.descending;
  loading.value = true;
  error.value = "";
  try {
    const page = await api.workstations({
      search: search.value || undefined,
      ou: ou.value || undefined,
      reachability: reachability.value || undefined,
      accountStatus: accountStatus.value || undefined,
      scopeOnly: scopeOnly.value,
      sort: sortBy || "computerName",
      sortDir: descending ? "desc" : "asc",
      page: pageNum,
      pageSize: rowsPerPage,
    });
    loadFailed.value = false;
    rows.value = page.items;
    total.value = page.total;
    summary.value = page.summary;
    ous.value = page.ous;
    activeSyncOus.value = page.activeSyncOus || [];
    // Mutate in place so Quasar keeps server-side pagination controls working.
    pagination.value.rowsNumber = page.total;
    pagination.value.page = page.page;
    pagination.value.rowsPerPage = page.pageSize;
    pagination.value.sortBy = sortBy || "computerName";
    pagination.value.descending = descending;
    const keep = new Set(selectedRows.value.map((r) => r.id));
    selectedRows.value = rows.value.filter((r) => keep.has(r.id));
  } catch (err) {
    loadFailed.value = true;
    rows.value = [];
    selectedRows.value = [];
    total.value = 0;
    ous.value = [];
    pagination.value.rowsNumber = 0;
    summary.value = { totalPresent: 0, assigned: 0, reachable: 0, noReply: 0, dnsFailed: 0, unchecked: 0 };
    error.value = formatApiFailure(err, t("workstations.loadError"));
  } finally {
    loading.value = false;
  }
}

async function openSyncDialog() {
  syncOpen.value = true;
  error.value = "";
  syncOuSearch.value = "";
  syncParentDn.value = "";
  await loadSyncOus();
}

function toggleOu(item: { name: string; distinguishedName: string }, selected: boolean) {
  const next = { ...selectedOuMap.value };
  if (selected) {
    next[item.distinguishedName] = {
      name: item.name,
      distinguishedName: item.distinguishedName,
    };
  } else {
    delete next[item.distinguishedName];
  }
  selectedOuMap.value = next;
}

function removeSelectedOu(dn: string) {
  const next = { ...selectedOuMap.value };
  delete next[dn];
  selectedOuMap.value = next;
}

function setAllVisibleOus(selected: boolean) {
  const next = { ...selectedOuMap.value };
  for (const item of syncOuItems.value) {
    if (selected) {
      next[item.distinguishedName] = {
        name: item.name,
        distinguishedName: item.distinguishedName,
      };
    } else {
      delete next[item.distinguishedName];
    }
  }
  selectedOuMap.value = next;
}

function clearSelectedOus() {
  selectedOuMap.value = {};
}

async function openOuChildren(dn: string) {
  syncOuSearch.value = "";
  syncParentDn.value = dn;
  await loadSyncOus(dn);
}

async function goOuUp() {
  const parent = syncParentDn.value || syncBaseDn.value;
  if (!parent || parent.toLowerCase() === (syncBaseDn.value || "").toLowerCase()) {
    syncParentDn.value = syncBaseDn.value;
    await loadSyncOus(syncBaseDn.value);
    return;
  }
  const parts = parent.split(",").map((p) => p.trim()).filter(Boolean);
  const up = parts.slice(1).join(",");
  syncParentDn.value = up || syncBaseDn.value;
  await loadSyncOus(syncParentDn.value);
}

async function loadSyncOus(parentDn?: string) {
  syncOuLoading.value = true;
  error.value = "";
  try {
    const page = await api.workstationSyncOuOptions({
      connectionId: syncConnectionId.value || undefined,
      parentDn: parentDn || syncParentDn.value || undefined,
      search: syncOuSearch.value || undefined,
    });
    syncConnectionId.value = page.connectionId;
    syncConnectionName.value = page.connectionName;
    syncBaseDn.value = page.baseDn;
    syncParentDn.value = page.parentDn || page.baseDn;
    syncOuItems.value = page.items.map((i) => ({ ...i }));
    // Seed basket from saved scope only when opening at domain root with empty basket.
    if (!Object.keys(selectedOuMap.value).length && page.selectedItems?.length) {
      const seeded: Record<string, { name: string; distinguishedName: string }> = {};
      for (const item of page.selectedItems) {
        seeded[item.distinguishedName] = {
          name: item.name,
          distinguishedName: item.distinguishedName,
        };
      }
      selectedOuMap.value = seeded;
    }
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.loadOusError"));
  } finally {
    syncOuLoading.value = false;
  }
}

async function confirmSyncInventory() {
  const selected = Object.values(selectedOuMap.value);
  const ouDns = selected.map((i) => i.distinguishedName);
  if (!ouDns.length) {
    error.value = t("workstations.ouRequired");
    return;
  }
  syncing.value = true;
  error.value = "";
  syncNote.value = "";
  try {
    await api.saveWorkstationOuScope({
      connectionId: syncConnectionId.value || undefined,
      items: selected.map((i) => ({
        name: i.name,
        distinguishedName: i.distinguishedName,
        selected: true,
      })),
    });
    const result = await api.syncWorkstations({
      connectionId: syncConnectionId.value || undefined,
      ouDns,
      includeServers: false,
      persistOuScope: true,
    });
    syncOpen.value = false;
    syncNote.value = t("workstations.syncDone", {
      discovered: result.discovered,
      created: result.created,
      updated: result.updated,
      missing: result.missingMarked,
      ous: result.syncedOus?.length || ouDns.length,
    });
    pagination.value.page = 1;
    await reload();
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.syncError"));
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
    error.value = formatApiFailure(err, t("workstations.pingError"));
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
    error.value = formatApiFailure(err, t("workstations.pingError"));
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
    error.value = formatApiFailure(err, t("workstations.assignError"));
  } finally {
    busy.value = false;
  }
}

async function loadDeployPrereqs() {
  try {
    deployPrereqs.value = await api.workstationDeployPrerequisites();
  } catch {
    deployPrereqs.value = null;
  }
}

function openDeploy() {
  if (!canDeploySelected.value) return;
  deployConfirmation.value = "";
  deployOpen.value = true;
}

async function confirmDeploy() {
  if (!canDeploySelected.value || !deployPrereqs.value) return;
  deploying.value = true;
  error.value = "";
  try {
    const result = await api.deployWorkstationAgents({
      workstationIds: selectedRows.value.map((r) => r.id),
      confirmation: deployConfirmation.value,
      approve: true,
    });
    deployOpen.value = false;
    syncNote.value = result.note;
    const withTokens = (result.jobs || [])
      .filter((j) => j.machineEnrollmentToken)
      .map((j) => ({
        id: j.id,
        computerName: j.computerName,
        machineEnrollmentToken: String(j.machineEnrollmentToken),
      }));
    if (withTokens.length) {
      tokenJobs.value = withTokens;
      tokenInstruction.value = result.tokenProvisioning?.instruction || t("workstations.tokenHint");
      tokenOpen.value = true;
    }
    await reload();
    await loadDeployPrereqs();
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.deployError"));
  } finally {
    deploying.value = false;
  }
}

function closeTokenDialog() {
  tokenOpen.value = false;
  tokenJobs.value = [];
  tokenInstruction.value = "";
}

async function cancelDeploy(row: Workstation) {
  const jobId = row.deploymentJob?.id;
  if (!jobId) return;
  jobBusyId.value = jobId;
  error.value = "";
  try {
    await api.cancelWorkstationDeploy(jobId);
    await reload();
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.deployError"));
  } finally {
    jobBusyId.value = null;
  }
}

async function retryDeploy(row: Workstation) {
  const jobId = row.deploymentJob?.id;
  if (!jobId) return;
  jobBusyId.value = jobId;
  error.value = "";
  try {
    await api.retryWorkstationDeploy(jobId);
    await reload();
    await loadDeployPrereqs();
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.deployError"));
  } finally {
    jobBusyId.value = null;
  }
}

async function onRequest(req: {
  pagination: { page: number; rowsPerPage: number; sortBy?: string | null; descending?: boolean };
}) {
  const { page, rowsPerPage, sortBy, descending } = req.pagination;
  await reload({
    page,
    rowsPerPage: rowsPerPage || 25,
    sortBy: sortBy || "computerName",
    descending: !!descending,
  });
}

function onScopeToggle() {
  void reload({ page: 1 });
}

watch([ou, reachability, accountStatus], () => {
  void reload({ page: 1 });
});

onMounted(async () => {
  try {
    const userPage = await api.listDirectoryUsers({ pageSize: 200 });
    employees.value = userPage.items;
  } catch {
    employees.value = [];
  }
  await Promise.all([reload(), loadDeployPrereqs()]);
});
</script>
