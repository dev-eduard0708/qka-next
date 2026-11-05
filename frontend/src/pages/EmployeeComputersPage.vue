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

    <div class="row q-col-gutter-md q-mb-md items-center">
      <div class="col-12 col-md-5">
        <q-select
          v-model="filterUserId"
          :options="employeeFilterOptions"
          emit-value
          map-options
          clearable
          outlined
          dense
          :label="t('desktop.filterEmployee')"
          @update:model-value="onFilterChange"
        />
      </div>
      <div class="col-12 col-md-7 row q-gutter-sm items-center">
        <q-btn
          color="primary"
          outline
          :disable="!canBrowseFolders"
          :loading="browseBusy"
          :label="t('desktop.browseFolders')"
          @click="openBrowse"
        >
          <q-tooltip v-if="!canBrowseFolders">{{ browseBlockedReason }}</q-tooltip>
        </q-btn>
        <q-btn
          color="primary"
          unelevated
          :loading="busy"
          :label="t('desktop.issueDeviceEnrollment')"
          @click="issueDeviceEnrollment"
        >
          <q-tooltip>{{ t("desktop.issueDeviceEnrollmentHint") }}</q-tooltip>
        </q-btn>
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
        <div class="text-caption text-grey-7 q-mb-sm">
          {{ selectedDevice.domainJoinedName || selectedDevice.computerName }}
          ·
          <span :class="selectedDevice.employeeAssigned === false ? 'text-warning text-weight-medium' : ''">
            {{ selectedDevice.employeeAssigned === false ? t("desktop.employeeUnassigned") : selectedDevice.employeeName }}
          </span>
        </div>

        <div class="row q-col-gutter-sm items-center q-mb-md">
          <div class="col-12 col-md-6">
            <q-select
              v-model="assignUserId"
              :options="employeeOptions"
              emit-value
              map-options
              clearable
              outlined
              dense
              :label="t('desktop.assignEmployee')"
            />
          </div>
          <div class="col-auto">
            <q-btn
              color="primary"
              unelevated
              :loading="busy"
              :disable="!assignUserId && selectedDevice.employeeAssigned !== false"
              :label="t('desktop.saveAssignment')"
              @click="saveAssignment"
            />
          </div>
          <div class="col-auto" v-if="selectedDevice.employeeAssigned">
            <q-btn flat color="warning" :loading="busy" :label="t('desktop.clearAssignment')" @click="clearAssignment" />
          </div>
        </div>
        <div v-if="selectedDevice.employeeAssigned === false" class="text-caption text-warning q-mb-md">
          {{ t("desktop.unassignedHint") }}
        </div>

        <div class="text-subtitle2 q-mb-xs">{{ t("desktop.currentApproved") }}</div>
        <div v-if="selectedDevice.folders?.length" class="q-mb-md">
          <q-chip
            v-for="f in selectedDevice.folders"
            :key="f.id"
            dense
            color="primary"
            text-color="white"
            class="q-ma-xs"
            :label="f.path"
          />
        </div>
        <div v-else class="text-caption text-grey-7 q-mb-md">{{ t("desktop.noApprovedFolders") }}</div>

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
          <q-btn
            dense
            color="primary"
            unelevated
            :loading="busy"
            :disable="selectedDevice.employeeAssigned === false"
            :label="t('desktop.start')"
            @click="command('start')"
          />
          <q-btn dense outline color="primary" :loading="busy" :label="t('desktop.pause')" @click="command('pause')" />
          <q-btn
            dense
            outline
            color="primary"
            :loading="busy"
            :disable="selectedDevice.employeeAssigned === false"
            :label="t('desktop.resume')"
            @click="command('resume')"
          />
          <q-btn dense outline color="negative" :loading="busy" :label="t('desktop.revoke')" @click="revoke" />
        </div>

        <div class="text-caption text-grey-6 q-mt-md">{{ t("desktop.gpoNote") }}</div>
        <div class="text-caption text-grey-6">{{ selectedDevice.gpoDeployment?.note }}</div>
      </q-card-section>

      <q-card-section v-else>
        <div class="text-body2 text-grey-7">{{ t("desktop.selectDeviceHint") }}</div>
      </q-card-section>
    </q-card>

    <q-dialog v-model="browseOpen" persistent>
      <q-card style="min-width: 560px; max-width: 760px">
        <q-card-section class="text-h6">{{ t("desktop.browseTitle") }}</q-card-section>
        <q-card-section>
          <div class="text-caption q-mb-sm">
            {{ selectedDevice?.domainJoinedName || selectedDevice?.computerName }}
            · {{ selectedDevice?.employeeName }}
          </div>
          <div class="text-caption text-grey-7 q-mb-md">{{ t("desktop.browseHint") }}</div>

          <div class="row q-gutter-sm q-mb-md">
            <q-btn
              v-for="shortcut in knownShortcuts"
              :key="shortcut"
              dense
              outline
              color="primary"
              :label="shortcut"
              :disable="browseBusy || !shortcutPath(shortcut)"
              @click="selectKnown(shortcut)"
            />
          </div>

          <q-banner v-if="browseError" class="bg-warning text-dark q-mb-sm">{{ browseError }}</q-banner>
          <q-linear-progress v-if="browseBusy" indeterminate color="primary" class="q-mb-sm" />

          <q-tree
            :nodes="treeNodes"
            node-key="pathKey"
            :expanded="expandedKeys"
            @update:expanded="onExpanded"
            children-key="children"
          >
            <template #default-header="prop">
              <div class="row items-center no-wrap full-width">
                <q-checkbox
                  dense
                  :model-value="isSelected(prop.node.path)"
                  :disable="!prop.node.accessible || !prop.node.path"
                  @update:model-value="(v: boolean) => toggleSelect(prop.node, v)"
                />
                <q-icon :name="prop.node.kind === 'known_folder' ? 'folder_special' : 'folder'" class="q-mx-xs" size="18px" />
                <span>{{ prop.node.label }}</span>
                <span v-if="!prop.node.accessible" class="text-caption text-negative q-ml-sm">{{ t("desktop.inaccessible") }}</span>
              </div>
            </template>
          </q-tree>

          <q-checkbox v-model="includeSubfolders" dense class="q-mt-md" :label="t('desktop.includeSubfolders')" />

          <div class="text-subtitle2 q-mt-md q-mb-xs">{{ t("desktop.selectedFolders") }}</div>
          <div v-if="selectedFolderPaths.length" class="q-gutter-xs">
            <q-chip
              v-for="p in selectedFolderPaths"
              :key="p"
              removable
              dense
              color="primary"
              text-color="white"
              :label="p"
              @remove="removeSelected(p)"
            />
          </div>
          <div v-else class="text-caption text-grey-7">{{ t("desktop.noneSelectedFolders") }}</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat :label="t('desktop.cancel')" v-close-popup />
          <q-btn
            color="primary"
            unelevated
            :loading="busy"
            :disable="!selectedFolderPaths.length"
            :label="t('desktop.saveApproved')"
            @click="saveSelectedFolders"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { api, type DesktopDevice, type DirectoryUser, type FolderBrowseNode } from "@/api";

type TreeNode = {
  label: string;
  path: string;
  pathKey: string;
  kind: string;
  accessible: boolean;
  hasChildren: boolean;
  detail?: string;
  lazy?: boolean;
  children?: TreeNode[];
};

const { t } = useI18n();
const loading = ref(false);
const busy = ref(false);
const browseBusy = ref(false);
const error = ref("");
const browseError = ref("");
const devices = ref<DesktopDevice[]>([]);
const employees = ref<DirectoryUser[]>([]);
const filterUserId = ref<string | null>(null);
const assignUserId = ref<string | null>(null);
const selectedRows = ref<DesktopDevice[]>([]);
const enrollmentCode = ref("");
const enrollmentExpires = ref("");
const browseOpen = ref(false);
const treeNodes = ref<TreeNode[]>([]);
const expandedKeys = ref<string[]>([]);
const selectedFolderPaths = ref<string[]>([]);
const includeSubfolders = ref(true);
const knownMap = ref<Record<string, string>>({});
const childrenCache = ref<Record<string, TreeNode[]>>({});
let pollTimer: number | undefined;

const knownShortcuts = ["Desktop", "Documents", "Downloads"];

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

const employeeFilterOptions = computed(() => [
  { label: t("desktop.filterAllDevices"), value: "" },
  { label: t("desktop.filterUnassigned"), value: "__unassigned__" },
  ...employeeOptions.value,
]);

const selectedDevice = computed(() => selectedRows.value[0] || null);

const canBrowseFolders = computed(
  () =>
    Boolean(
      selectedDevice.value?.folderBrowseAvailable &&
        selectedDevice.value?.online &&
        selectedDevice.value?.employeeAssigned !== false,
    ),
);

const browseBlockedReason = computed(() => {
  if (!selectedDevice.value) return t("desktop.selectDeviceHint");
  return selectedDevice.value.folderBrowseBlockedReason || t("desktop.browseOffline");
});

const indexBadgeColor = computed(() => {
  const state = selectedDevice.value?.indexingState;
  if (selectedDevice.value?.employeeAssigned === false || state === "disabled") return "grey-7";
  if (state === "running") return "positive";
  if (state === "paused") return "warning";
  if (state === "error") return "negative";
  return "grey";
});

const indexBadgeLabel = computed(() => {
  if (selectedDevice.value?.employeeAssigned === false) return t("desktop.index.disabled");
  const state = selectedDevice.value?.indexingState || "idle";
  return t(`desktop.index.${state}`);
});

function shortcutPath(name: string) {
  return knownMap.value[name] || "";
}

function isSelected(path: string) {
  return Boolean(path) && selectedFolderPaths.value.includes(path);
}

function toggleSelect(node: TreeNode, checked: boolean) {
  if (!node.path || !node.accessible) return;
  const set = new Set(selectedFolderPaths.value);
  if (checked) set.add(node.path);
  else set.delete(node.path);
  selectedFolderPaths.value = [...set];
}

function removeSelected(path: string) {
  selectedFolderPaths.value = selectedFolderPaths.value.filter((p) => p !== path);
}

function selectKnown(name: string) {
  const path = knownMap.value[name];
  if (!path) return;
  if (!selectedFolderPaths.value.includes(path)) {
    selectedFolderPaths.value = [...selectedFolderPaths.value, path];
  }
}

function toTreeNode(n: FolderBrowseNode): TreeNode {
  const path = n.path || `virtual:${n.name}`;
  return {
    label: n.name,
    path: n.path || "",
    pathKey: path,
    kind: n.kind,
    accessible: n.accessible,
    hasChildren: n.hasChildren,
    detail: n.detail,
    lazy: Boolean(n.hasChildren && n.path),
    children: n.hasChildren && n.path ? childrenCache.value[n.path] || [] : undefined,
  };
}

async function waitBrowse(deviceId: string, browseId: string) {
  const started = Date.now();
  while (Date.now() - started < 45000) {
    const result = await api.getDesktopFolderBrowse(deviceId, browseId);
    if (result.status === "completed" || result.status === "failed" || result.status === "expired") {
      return result;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error(t("desktop.browseTimeout"));
}

async function loadRoots() {
  if (!selectedDevice.value) return;
  browseBusy.value = true;
  browseError.value = "";
  try {
    const req = await api.requestDesktopFolderBrowse(selectedDevice.value.id, "");
    const result = await waitBrowse(selectedDevice.value.id, req.id);
    if (result.error) browseError.value = result.error;
    knownMap.value = {};
    for (const n of result.nodes) {
      if (n.kind === "known_folder" && n.accessible && n.path) {
        knownMap.value[n.name] = n.path;
      }
    }
    treeNodes.value = result.nodes.map(toTreeNode);
    expandedKeys.value = treeNodes.value.filter((n) => n.path).map((n) => n.pathKey).slice(0, 1);
  } catch (err) {
    browseError.value = err instanceof Error ? err.message : t("desktop.browseError");
    treeNodes.value = [];
  } finally {
    browseBusy.value = false;
  }
}

async function loadChildren(parentPath: string) {
  if (!selectedDevice.value || !parentPath) return;
  if (childrenCache.value[parentPath]) return;
  browseBusy.value = true;
  try {
    const req = await api.requestDesktopFolderBrowse(selectedDevice.value.id, parentPath);
    const result = await waitBrowse(selectedDevice.value.id, req.id);
    childrenCache.value = {
      ...childrenCache.value,
      [parentPath]: result.nodes.map(toTreeNode),
    };
    // Refresh tree nodes to attach children
    treeNodes.value = treeNodes.value.map((n) => attachChildren(n));
  } catch (err) {
    browseError.value = err instanceof Error ? err.message : t("desktop.browseError");
  } finally {
    browseBusy.value = false;
  }
}

function attachChildren(node: TreeNode): TreeNode {
  if (!node.path) return node;
  const kids = childrenCache.value[node.path];
  return {
    ...node,
    children: kids ? kids.map(attachChildren) : node.children,
  };
}

function onExpanded(keys: readonly any[]) {
  expandedKeys.value = keys.map((k) => String(k));
  for (const key of expandedKeys.value) {
    if (key.startsWith("virtual:")) continue;
    if (!childrenCache.value[key]) {
      void loadChildren(key);
    }
  }
}

function openBrowse() {
  if (!canBrowseFolders.value || !selectedDevice.value) return;
  selectedFolderPaths.value = (selectedDevice.value.folders || []).map((f) => f.path);
  includeSubfolders.value = true;
  childrenCache.value = {};
  treeNodes.value = [];
  browseOpen.value = true;
  void loadRoots();
}

async function saveSelectedFolders() {
  if (!selectedDevice.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api.setDesktopDeviceFolders(
      selectedDevice.value.id,
      selectedFolderPaths.value.map((path) => ({ path, includeSubfolders: includeSubfolders.value })),
    );
    browseOpen.value = false;
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.saveError");
  } finally {
    busy.value = false;
  }
}

async function reload() {
  loading.value = true;
  error.value = "";
  try {
    const filter = filterUserId.value;
    const qkaFilter = filter && filter !== "__unassigned__" && filter !== "" ? filter : undefined;
    const [devicePage, userPage] = await Promise.all([
      api.desktopDevices(qkaFilter),
      api.listDirectoryUsers({ pageSize: 200 }),
    ]);
    let items = devicePage.items;
    if (filter === "__unassigned__") {
      items = items.filter((d) => d.employeeAssigned === false || !d.qkaUserId);
    }
    devices.value = items;
    employees.value = userPage.items;
    if (selectedDevice.value) {
      const keep = devices.value.find((d) => d.id === selectedDevice.value?.id);
      selectedRows.value = keep ? [keep] : [];
      assignUserId.value = keep?.qkaUserId || null;
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.loadError");
  } finally {
    loading.value = false;
  }
}

function onFilterChange() {
  selectedRows.value = [];
  void reload();
}

async function issueDeviceEnrollment() {
  busy.value = true;
  error.value = "";
  try {
    const created = await api.createDesktopEnrollmentCode({ label: "device-registration" });
    enrollmentCode.value = created.enrollmentCode;
    enrollmentExpires.value = created.expiresAt;
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.enrollError");
  } finally {
    busy.value = false;
  }
}

async function saveAssignment() {
  if (!selectedDevice.value || !assignUserId.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api.assignDesktopDeviceEmployee(selectedDevice.value.id, assignUserId.value);
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.assignError");
  } finally {
    busy.value = false;
  }
}

async function clearAssignment() {
  if (!selectedDevice.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api.assignDesktopDeviceEmployee(selectedDevice.value.id, null);
    assignUserId.value = null;
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.assignError");
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

watch(selectedRows, () => {
  assignUserId.value = selectedDevice.value?.qkaUserId || null;
});

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
