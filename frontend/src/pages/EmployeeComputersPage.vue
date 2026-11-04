<template>
  <q-page padding class="qka-employee-computers">
    <div class="row items-start q-mb-md">
      <div class="col">
        <div class="text-caption text-grey-7">{{ t("desktop.breadcrumb") }}</div>
        <h1 class="text-h5 q-my-none">{{ t("desktop.title") }}</h1>
        <p class="text-body2 text-grey-7 q-mt-sm q-mb-none">{{ t("desktop.body") }}</p>
      </div>
      <div class="col-auto">
        <q-btn color="primary" unelevated :loading="busy" :label="t('desktop.refresh')" @click="reload" />
      </div>
    </div>

    <q-tabs dense class="q-mb-md" active-color="primary" indicator-color="primary" align="left">
      <q-route-tab :label="t('workstations.tabAgents')" :to="{ name: 'employee-computers' }" />
      <q-route-tab :label="t('workstations.tabWorkstations')" :to="{ name: 'workstations' }" />
    </q-tabs>

    <q-banner v-if="error" class="bg-negative text-white q-mb-md">{{ error }}</q-banner>

    <div class="row q-col-gutter-md q-mb-md">
      <div class="col-12 col-md-4">
        <q-select
          v-model="selectedUserId"
          :options="employeeOptions"
          emit-value
          map-options
          outlined
          dense
          :label="t('desktop.selectEmployee')"
          @update:model-value="onEmployeeChange"
        />
      </div>
      <div class="col-12 col-md-4">
        <q-input v-model="folderDraft" outlined dense :label="t('desktop.approvedFolder')" />
      </div>
      <div class="col-12 col-md-4 row q-gutter-sm items-center">
        <q-btn color="primary" outline :disable="!selectedDevice" :loading="busy" :label="t('desktop.saveFolders')" @click="saveFolders" />
        <q-btn color="primary" unelevated :disable="!selectedUserId" :loading="busy" :label="t('desktop.issueEnrollment')" @click="issueEnrollment" />
      </div>
    </div>

    <q-banner v-if="enrollmentCode" class="bg-grey-2 q-mb-md">
      <div class="text-subtitle2">{{ t("desktop.enrollmentIssued") }}</div>
      <div class="text-caption">{{ t("desktop.enrollmentHint") }}</div>
      <code class="text-body2">{{ enrollmentCode }}</code>
      <div v-if="enrollmentExpires" class="text-caption q-mt-xs">{{ t("desktop.expires") }}: {{ enrollmentExpires }}</div>
    </q-banner>

    <q-card bordered>
      <q-card-section>
        <q-table
          flat
          dense
          row-key="id"
          :rows="devices"
          :columns="columns"
          :loading="loading"
          selection="single"
          v-model:selected="selectedRows"
          :pagination="{ rowsPerPage: 10 }"
        >
          <template #body-cell-agentStatus="props">
            <q-td :props="props">
              <span :class="props.row.online ? 'text-positive text-weight-medium' : 'text-grey-7'">
                {{ props.row.online ? t("desktop.connected") : t("desktop.offline") }}
              </span>
            </q-td>
          </template>
          <template #body-cell-indexed="props">
            <q-td :props="props">
              {{ props.row.filesIndexed.toLocaleString() }} {{ t("desktop.files") }}
            </q-td>
          </template>
        </q-table>
      </q-card-section>

      <q-separator />

      <q-card-section v-if="selectedDevice">
        <div class="row items-center q-mb-sm">
          <div class="col text-subtitle1">{{ t("desktop.backgroundIndexing") }}</div>
          <div class="col-auto">
            <q-badge :color="indexBadgeColor" :label="indexBadgeLabel" />
          </div>
        </div>
        <q-linear-progress
          size="10px"
          rounded
          :value="(selectedDevice.percentComplete || 0) / 100"
          color="positive"
          track-color="grey-3"
          class="q-mb-xs"
        />
        <div class="row text-caption text-grey-8 q-mb-md">
          <div class="col">
            {{ selectedDevice.filesIndexed.toLocaleString() }}
            {{ t("desktop.of") }}
            {{ selectedDevice.filesTotal.toLocaleString() }}
            {{ t("desktop.filesProcessed") }}
          </div>
          <div class="col-auto">{{ selectedDevice.percentComplete || 0 }}%</div>
        </div>

        <div class="row items-center q-gutter-xs q-mb-md">
          <q-icon
            :name="selectedDevice.readOnlyVerified ? 'check_circle' : 'error'"
            :color="selectedDevice.readOnlyVerified ? 'positive' : 'negative'"
            size="18px"
          />
          <span :class="selectedDevice.readOnlyVerified ? 'text-positive' : 'text-negative'">
            {{ selectedDevice.readOnlyVerified ? t("desktop.readOnlyVerified") : t("desktop.readOnlyFailed") }}
          </span>
        </div>
        <div v-if="selectedDevice.readOnlyDetail" class="text-caption text-grey-7 q-mb-sm">
          {{ selectedDevice.readOnlyDetail }}
        </div>
        <div v-if="selectedDevice.lastError" class="text-caption text-negative q-mb-sm">
          {{ selectedDevice.lastError }}
        </div>

        <div class="text-caption text-grey-7 q-mb-md">
          {{ t("desktop.lastHeartbeat") }}:
          {{ selectedDevice.lastHeartbeatAt || t("desktop.never") }}
          · {{ t("desktop.agentVersion") }}: {{ selectedDevice.agentVersion || "—" }}
        </div>

        <div class="row q-gutter-sm">
          <q-btn dense color="primary" unelevated :loading="busy" :label="t('desktop.start')" @click="command('start')" />
          <q-btn dense outline color="primary" :loading="busy" :label="t('desktop.pause')" @click="command('pause')" />
          <q-btn dense outline color="primary" :loading="busy" :label="t('desktop.resume')" @click="command('resume')" />
          <q-btn dense outline color="negative" :loading="busy" :label="t('desktop.revoke')" @click="revoke" />
        </div>

        <div class="text-caption text-grey-6 q-mt-md">{{ t("desktop.gpoNote") }}</div>
        <div class="text-caption text-grey-6">{{ selectedDevice.gpoDeployment?.note }}</div>
      </q-card-section>

      <q-card-section v-else>
        <div class="text-body2 text-grey-7">{{ t("desktop.selectDeviceHint") }}</div>
      </q-card-section>
    </q-card>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api, type DesktopDevice, type DirectoryUser } from "@/api";

const { t } = useI18n();
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const devices = ref<DesktopDevice[]>([]);
const employees = ref<DirectoryUser[]>([]);
const selectedUserId = ref<string | null>(null);
const selectedRows = ref<DesktopDevice[]>([]);
const folderDraft = ref("");
const enrollmentCode = ref("");
const enrollmentExpires = ref("");
let pollTimer: number | undefined;

const columns = [
  { name: "employeeName", label: t("desktop.employee"), field: "employeeName", align: "left" as const },
  {
    name: "computerName",
    label: t("desktop.computer"),
    field: (row: DesktopDevice) => row.domainJoinedName || row.computerName || "—",
    align: "left" as const,
  },
  { name: "agentStatus", label: t("desktop.agent"), field: "agentStatus", align: "left" as const },
  { name: "indexed", label: t("desktop.indexed"), field: "filesIndexed", align: "left" as const },
  { name: "status", label: t("desktop.status"), field: "status", align: "left" as const },
];

const employeeOptions = computed(() =>
  employees.value
    .filter((e) => e.qkaUserId && (e.linkedIdentities || "").includes("ad"))
    .map((e) => ({ label: `${e.displayName} (${e.primaryEmail || e.qkaUserId})`, value: e.qkaUserId as string })),
);

const selectedDevice = computed(() => selectedRows.value[0] || null);

const indexBadgeColor = computed(() => {
  const state = selectedDevice.value?.indexingState;
  if (state === "running") return "positive";
  if (state === "paused") return "warning";
  if (state === "error") return "negative";
  return "grey";
});

const indexBadgeLabel = computed(() => {
  const state = selectedDevice.value?.indexingState || "idle";
  return t(`desktop.index.${state}`);
});

async function reload() {
  loading.value = true;
  error.value = "";
  try {
    const [devicePage, userPage] = await Promise.all([
      api.desktopDevices(selectedUserId.value || undefined),
      api.listDirectoryUsers({ pageSize: 200 }),
    ]);
    devices.value = devicePage.items;
    employees.value = userPage.items;
    if (selectedDevice.value) {
      const keep = devices.value.find((d) => d.id === selectedDevice.value?.id);
      selectedRows.value = keep ? [keep] : [];
      if (keep?.folders?.length) {
        folderDraft.value = keep.folders.map((f) => f.path).join("; ");
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.loadError");
  } finally {
    loading.value = false;
  }
}

function onEmployeeChange() {
  void reload();
}

async function issueEnrollment() {
  if (!selectedUserId.value) return;
  busy.value = true;
  error.value = "";
  try {
    const created = await api.createDesktopEnrollmentCode({ qkaUserId: selectedUserId.value });
    enrollmentCode.value = created.enrollmentCode;
    enrollmentExpires.value = created.expiresAt;
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.enrollError");
  } finally {
    busy.value = false;
  }
}

async function saveFolders() {
  if (!selectedDevice.value) return;
  busy.value = true;
  error.value = "";
  try {
    const folders = folderDraft.value
      .split(";")
      .map((p) => p.trim())
      .filter(Boolean)
      .map((path) => ({ path, includeSubfolders: true }));
    await api.setDesktopDeviceFolders(selectedDevice.value.id, folders);
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.saveError");
  } finally {
    busy.value = false;
  }
}

async function command(cmd: "start" | "pause" | "resume") {
  if (!selectedDevice.value) return;
  busy.value = true;
  try {
    await api.desktopDeviceCommand(selectedDevice.value.id, cmd);
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.commandError");
  } finally {
    busy.value = false;
  }
}

async function revoke() {
  if (!selectedDevice.value) return;
  busy.value = true;
  try {
    await api.revokeDesktopDevice(selectedDevice.value.id);
    selectedRows.value = [];
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.revokeError");
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  await reload();
  pollTimer = window.setInterval(() => {
    void reload();
  }, 15000);
});

onUnmounted(() => {
  if (pollTimer) window.clearInterval(pollTimer);
});
</script>
