<template>
  <q-page padding class="qka-synology">
    <div class="row items-center q-mb-md">
      <div class="col">
        <div class="text-caption text-grey-7">{{ t("nav.settings") }} / {{ t("nav.integrations") }}</div>
        <h1 class="text-h5 q-my-none">{{ t("synology.title") }}</h1>
      </div>
    </div>
    <p class="text-body2 q-mb-lg">{{ t("synology.body") }}</p>

    <q-banner v-if="!session.user?.superAdmin" class="bg-warning text-dark q-mb-md">
      {{ t("synology.adminOnly") }}
    </q-banner>
    <q-banner v-if="error" class="bg-negative text-white q-mb-md">{{ error }}</q-banner>
    <q-banner v-if="testMessage" :class="testOk ? 'bg-positive text-white' : 'bg-negative text-white'" class="q-mb-md">
      {{ testMessage }}
    </q-banner>

    <div v-if="session.user?.superAdmin" class="qka-synology-stack">
      <q-card bordered>
        <q-card-section class="row items-center q-col-gutter-sm">
          <div class="col">
            <div class="text-subtitle1">{{ t("synology.connections") }}</div>
            <div class="text-caption">{{ t("synology.connectionsHint") }}</div>
          </div>
          <div class="col-auto">
            <q-btn
              color="primary"
              :unelevated="!(creating && connectionOpen)"
              :outline="creating && connectionOpen"
              :label="t('synology.addConnection')"
              @click="toggleAdd"
            />
          </div>
        </q-card-section>

        <div v-if="creating && connectionOpen" class="q-px-md q-pb-md">
          <div class="qka-inline-form q-pa-md">
            <div class="row q-col-gutter-md">
              <div class="col-12 col-md-6">
                <q-input v-model="form.name" outlined :label="t('synology.connectionName')" :hint="t('synology.connectionNameHint')" />
              </div>
              <div class="col-12 col-md-6">
                <q-input v-model="form.sharedFolderPath" outlined :label="t('synology.uncPath')" hint="\\192.168.110.11\eduard" />
              </div>
              <div class="col-12 col-md-4">
                <q-select v-model="form.authMode" outlined emit-value map-options :options="authOptions" :label="t('synology.authMode')" />
              </div>
              <div class="col-12 col-md-4">
                <q-input v-model="form.username" outlined :disable="form.authMode !== 1" :label="t('synology.username')" />
              </div>
              <div class="col-12 col-md-4">
                <q-input
                  v-model="form.password"
                  outlined
                  type="password"
                  autocomplete="new-password"
                  :disable="form.authMode !== 1"
                  :label="t('synology.password')"
                  :hint="secretHint"
                />
              </div>
            </div>
            <div class="row items-center justify-end q-gutter-sm q-mt-md">
              <q-btn color="primary" unelevated :loading="saving" :label="t('synology.save')" @click="save" />
              <q-btn outline color="primary" :loading="testing" :label="t('synology.test')" @click="test" />
              <q-btn flat :label="t('synology.close')" @click="closeForm" />
            </div>
          </div>
        </div>

        <q-list v-if="connections.length" bordered separator class="q-mx-md q-mb-md">
          <template v-for="item in connections" :key="item.id">
            <q-item
              clickable
              v-ripple
              :active="item.id === selectedId && !isEditingRow(item.id)"
              active-class="bg-primary text-white"
              @click="selectConnection(item.id)"
            >
              <q-item-section>
                <q-item-label>{{ item.name }}</q-item-label>
                <q-item-label caption class="ellipsis">{{ item.configuredSharedFolderPath || "—" }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <div class="row items-center no-wrap q-gutter-xs">
                  <q-chip
                    dense
                    :color="chipColor(item.connectionStatus)"
                    text-color="white"
                    :label="chipLabel(item.connectionStatus)"
                  />
                  <q-btn
                    flat
                    dense
                    no-caps
                    size="sm"
                    :text-color="item.id === selectedId && !isEditingRow(item.id) ? 'white' : 'primary'"
                    :label="isEditingRow(item.id) ? t('synology.close') : t('synology.edit')"
                    @click.stop="toggleEdit(item.id)"
                  />
                </div>
              </q-item-section>
            </q-item>
            <div v-if="isEditingRow(item.id)" class="qka-inline-form q-pa-md">
              <div class="row q-col-gutter-md">
                <div class="col-12 col-md-6">
                  <q-input v-model="form.name" outlined :label="t('synology.connectionName')" :hint="t('synology.connectionNameHint')" />
                </div>
                <div class="col-12 col-md-6">
                  <q-input v-model="form.sharedFolderPath" outlined :label="t('synology.uncPath')" hint="\\192.168.110.11\eduard" />
                </div>
                <div class="col-12 col-md-4">
                  <q-select v-model="form.authMode" outlined emit-value map-options :options="authOptions" :label="t('synology.authMode')" />
                </div>
                <div class="col-12 col-md-4">
                  <q-input v-model="form.username" outlined :disable="form.authMode !== 1" :label="t('synology.username')" />
                </div>
                <div class="col-12 col-md-4">
                  <q-input
                    v-model="form.password"
                    outlined
                    type="password"
                    autocomplete="new-password"
                    :disable="form.authMode !== 1"
                    :label="t('synology.password')"
                    :hint="secretHint"
                  />
                </div>
              </div>
              <div class="text-caption q-mt-sm">
                {{ t("synology.secretConfigured") }}: {{ connection?.serviceAccountSecretConfigured ? t("synology.yes") : t("synology.no") }}
              </div>
              <div class="row items-center q-gutter-sm q-mt-md">
                <q-btn flat color="negative" :loading="removing" :label="t('synology.removeConnection')" @click="removeSelected" />
                <q-space />
                <q-btn flat :label="t('synology.close')" @click="closeForm" />
                <q-btn outline color="primary" :loading="testing" :label="t('synology.test')" @click="test" />
                <q-btn color="primary" unelevated :loading="saving" :label="t('synology.save')" @click="save" />
              </div>
            </div>
          </template>
        </q-list>
        <div v-else-if="!(creating && connectionOpen)" class="text-body2 q-px-md q-pb-md">{{ t("synology.noConnections") }}</div>
      </q-card>

      <div class="row q-col-gutter-md">
        <div v-for="card in statCards" :key="card.label" class="col-6 col-md-3">
          <q-card bordered>
            <q-card-section>
              <div class="text-caption">{{ card.label }}</div>
              <div class="text-h6">{{ card.value }}</div>
            </q-card-section>
          </q-card>
        </div>
      </div>

      <q-card bordered>
        <q-card-section>
          <div class="text-subtitle1 q-mb-sm">{{ t("synology.contentIndexing") }}</div>
          <p class="text-caption">{{ t("synology.contentIndexingHint") }}</p>
          <div class="row q-col-gutter-sm q-mb-md">
            <div class="col-6 col-md-3" v-for="card in extractionStatCards" :key="card.label">
              <div class="text-caption">{{ card.label }}</div>
              <div class="text-subtitle1">{{ card.value }}</div>
            </div>
          </div>
          <div class="text-caption q-mb-sm">
            OCR: {{ extractionDeps?.ocr?.available ? t("synology.depReady") : t("synology.depMissing") }}
            <span v-if="extractionDeps?.ocr?.languages?.length"> ({{ extractionDeps.ocr.languages.join(", ") }})</span>
            · LibreOffice: {{ extractionDeps?.libreOffice?.available ? t("synology.depReady") : t("synology.depMissing") }}
            · extractor v{{ extractionDeps?.extractorVersion ?? "—" }}
          </div>
          <div v-if="extractionDeps?.ocr?.installHint && !extractionDeps?.ocr?.available" class="text-negative text-caption q-mb-sm">
            {{ extractionDeps.ocr.installHint }}
          </div>
          <div class="row q-col-gutter-sm q-mb-sm">
            <div class="col-auto">
              <q-btn
                color="primary"
                unelevated
                no-caps
                :loading="extractionBusy"
                :label="t('synology.extractMissing')"
                @click="runExtractMissing"
              />
            </div>
            <div class="col-auto">
              <q-btn
                outline
                color="primary"
                no-caps
                :loading="extractionBusy"
                :label="t('synology.retryFailed')"
                @click="runRetryFailed"
              />
            </div>
            <div class="col-auto">
              <q-btn
                outline
                color="primary"
                no-caps
                :loading="extractionBusy"
                :disable="!selectedInventoryIds.length"
                :label="t('synology.reindexSelected')"
                @click="runReindexSelected"
              />
            </div>
          </div>
          <div class="row q-col-gutter-sm items-center">
            <div class="col-12 col-md-8">
              <q-input
                v-model="reindexFolderPrefix"
                outlined
                dense
                :label="t('synology.reindexFolderPrefix')"
                :hint="t('synology.reindexFolderHint')"
              />
            </div>
            <div class="col-auto">
              <q-btn
                outline
                color="primary"
                no-caps
                :loading="extractionBusy"
                :disable="!reindexFolderPrefix.trim()"
                :label="t('synology.reindexFolder')"
                @click="runReindexFolder"
              />
            </div>
          </div>
          <div v-if="extractionMessage" class="text-caption q-mt-sm" :class="extractionOk ? 'text-positive' : 'text-negative'">
            {{ extractionMessage }}
          </div>
          <div v-if="extractionStatus?.recentBatches?.length" class="text-caption q-mt-sm">
            {{ t("synology.lastExtractionBatch") }}:
            {{ extractionStatus.recentBatches[0].action }} ·
            q{{ extractionStatus.recentBatches[0].queuedCount }} /
            p{{ extractionStatus.recentBatches[0].processedCount }} /
            ok{{ extractionStatus.recentBatches[0].succeededCount }} /
            fail{{ extractionStatus.recentBatches[0].failedCount }} /
            skip{{ extractionStatus.recentBatches[0].skippedCount }}
          </div>
        </q-card-section>
      </q-card>

      <q-card bordered>
        <q-card-section>
          <div class="text-subtitle1 q-mb-sm">{{ t("synology.sync") }}</div>
          <p class="text-caption">{{ t("synology.syncHint") }}</p>
          <div class="row q-col-gutter-sm q-mb-md">
            <div class="col-auto">
              <q-btn color="primary" unelevated :disable="jobBusy" :loading="starting" :label="t('synology.syncNow')" @click="startSync" />
            </div>
            <div class="col-auto">
              <q-btn outline color="primary" :disable="jobBusy" :loading="starting" :label="t('synology.resync')" @click="startSync" />
            </div>
            <div class="col-auto">
              <q-btn outline color="negative" :disable="!jobBusy" :loading="cancelling" :label="t('synology.cancel')" @click="cancel" />
            </div>
          </div>
          <div v-if="monitor?.currentJob">
            <div class="text-body2">{{ monitor.currentJob.currentOperation || jobStatusLabel(monitor.currentJob.status) }}</div>
            <q-linear-progress
              v-if="monitor.currentJob.percentComplete != null"
              :value="monitor.currentJob.percentComplete / 100"
              class="q-mt-sm"
              color="primary"
            />
            <div class="text-caption q-mt-sm">
              {{ t("synology.discovered") }}: {{ monitor.currentJob.filesDiscovered }} ·
              {{ t("synology.processed") }}: {{ monitor.currentJob.filesProcessed }} ·
              {{ t("synology.added") }}: {{ monitor.currentJob.filesNew }} ·
              {{ t("synology.updated") }}: {{ monitor.currentJob.filesModified }} ·
              {{ t("synology.unchanged") }}: {{ monitor.currentJob.filesUnchanged }} ·
              {{ t("synology.removed") }}: {{ monitor.currentJob.filesRemoved }}
            </div>
            <div v-if="monitor.currentJob.safeError" class="text-negative q-mt-sm">{{ monitor.currentJob.safeError }}</div>
          </div>
          <div v-else class="text-caption">{{ t("synology.noJob") }}</div>
        </q-card-section>
      </q-card>

      <q-card bordered>
        <q-card-section>
          <div class="text-subtitle1 q-mb-sm">{{ t("synology.accessPolicy") }}</div>
          <p class="text-caption q-mb-md">{{ t("synology.accessPolicyHint") }}</p>
          <q-select
            v-model="policyShareId"
            outlined
            dense
            emit-value
            map-options
            :options="policyShareOptions"
            :label="t('access.selectSource')"
            class="q-mb-md"
            @update:model-value="onPolicyShareSelected"
          />
          <div v-if="!policyShareId" class="text-caption text-grey-7">{{ t("synology.policySelectShare") }}</div>
          <template v-else>
            <div class="text-subtitle2 q-mb-sm">{{ t("synology.accessModel") }}</div>
            <q-btn-toggle
              v-model="policyModel"
              spread
              no-caps
              unelevated
              toggle-color="primary"
              class="full-width q-mb-sm"
              :options="[
                { label: t('synology.modelSourceAcl'), value: 'source_acl' },
                { label: t('synology.modelQkaManaged'), value: 'qka_managed' },
              ]"
            />
            <p class="text-caption text-grey-7 q-mb-md">
              {{ policyModel === "qka_managed" ? t("synology.modelQkaManagedHint") : t("synology.modelSourceAclHint") }}
            </p>
            <div v-if="policyManagedScopes.length" class="q-mb-md">
              <div class="text-positive text-caption q-mb-sm">{{ t("synology.policyActive") }}</div>
              <q-list bordered separator dense>
                <q-item v-for="scope in policyManagedScopes" :key="scope.id">
                  <q-item-section>
                    <q-item-label>{{ scope.displayName || scope.folderPrefixNormalized }}</q-item-label>
                    <q-item-label caption>{{ scope.approvalReference }} · {{ scope.sourceOwnerEmail }}</q-item-label>
                  </q-item-section>
                  <q-item-section side>
                    <q-btn flat dense color="negative" no-caps :loading="policySaving" :label="t('synology.policyRevoke')" @click="revokePolicyScope(scope.id)" />
                  </q-item-section>
                </q-item>
              </q-list>
            </div>
            <div v-if="policyModel === 'qka_managed' && !policyManagedScopes.length" class="qka-inline-form q-pa-md">
              <div class="row q-col-gutter-md">
                <div class="col-12">
                  <q-input v-model="policyForm.folderPrefix" outlined dense :label="t('synology.policyFolderPrefix')" />
                </div>
                <div class="col-12 col-md-6">
                  <q-input v-model="policyForm.ownerName" outlined dense :label="t('synology.policyOwnerName')" />
                </div>
                <div class="col-12 col-md-6">
                  <q-input v-model="policyForm.ownerEmail" outlined dense type="email" :label="t('synology.policyOwnerEmail')" />
                </div>
                <div class="col-12 col-md-6">
                  <q-input v-model="policyForm.approvalReference" outlined dense :label="t('synology.policyApprovalRef')" />
                </div>
                <div class="col-12 col-md-6">
                  <q-input v-model="policyForm.notes" outlined dense :label="t('synology.policyNotes')" />
                </div>
                <div class="col-12">
                  <q-checkbox v-model="policyForm.serviceReadVerified" dense :label="t('synology.policyServiceRead')" />
                </div>
              </div>
              <div class="row justify-end q-mt-md">
                <q-btn color="primary" unelevated no-caps :loading="policySaving" :label="t('synology.policyActivate')" @click="activateQkaManaged" />
              </div>
            </div>
          </template>
        </q-card-section>
      </q-card>

      <q-card bordered>
        <q-card-section>
          <div class="text-subtitle1 q-mb-md">{{ t("synology.inventory") }}</div>
          <q-table
            class="qka-inventory"
            flat
            dense
            :rows="inventory.items"
            :columns="columns"
            :loading="loadingInventory"
            row-key="id"
            v-model:pagination="pagination"
            :rows-number="inventory.totalCount"
            binary-state-sort
            @request="onRequest"
          >
            <template #top>
              <div class="row q-col-gutter-sm full-width items-center">
                <div class="col-12 col-md-7">
                  <q-input v-model="search" outlined dense debounce="400" :label="t('synology.search')" @update:model-value="reloadInventory" />
                </div>
                <div class="col-12 col-md-5">
                  <q-select
                    v-model="fileType"
                    outlined
                    dense
                    emit-value
                    map-options
                    clearable
                    :options="fileTypeOptions"
                    :label="t('synology.fileType')"
                    @update:model-value="reloadInventory"
                  />
                </div>
              </div>
            </template>
            <template #body-cell-select="props">
              <q-td :props="props">
                <q-checkbox
                  dense
                  :model-value="selectedInventoryIds.includes(props.row.id)"
                  @update:model-value="(v: boolean) => toggleInventorySelect(props.row.id, v)"
                />
              </q-td>
            </template>
            <template #body-cell-fileName="props">
              <q-td :props="props" class="qka-ellipsis-cell">
                <button type="button" class="qka-file-link" @click="preview(props.row.id)">
                  {{ props.row.fileName }}
                </button>
                <q-tooltip>{{ props.row.fileName }}</q-tooltip>
              </q-td>
            </template>
            <template #body-cell-relativeFolderPath="props">
              <q-td :props="props" class="qka-ellipsis-cell">
                <span class="qka-ellipsis">{{ props.row.relativeFolderPath || "—" }}</span>
                <q-tooltip v-if="props.row.relativeFolderPath">{{ props.row.relativeFolderPath }}</q-tooltip>
              </q-td>
            </template>
          </q-table>
        </q-card-section>
      </q-card>
    </div>

    <q-dialog v-model="previewOpen">
      <q-card style="min-width: 60vw; max-width: 90vw">
        <q-card-section>
          <div class="text-subtitle1">{{ previewTitle }}</div>
          <pre class="qka-preview q-mt-md">{{ previewText }}</pre>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat :label="t('synology.close')" v-close-popup />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { QTableColumn, QTableProps } from "quasar";
import {
  api,
  type ExtractionStatus,
  type SourceScope,
  type SourceSource,
  type SynologyConnection,
  type SynologyInventoryPage,
  type SynologyMonitor,
} from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const error = ref("");
const testMessage = ref("");
const testOk = ref(false);
const saving = ref(false);
const testing = ref(false);
const starting = ref(false);
const cancelling = ref(false);
const loadingInventory = ref(false);
const extractionStatus = ref<ExtractionStatus | null>(null);
const extractionBusy = ref(false);
const extractionMessage = ref("");
const extractionOk = ref(false);
const selectedInventoryIds = ref<string[]>([]);
const reindexFolderPrefix = ref("");
const extractionDeps = computed(() => extractionStatus.value?.dependencies ?? null);
const extractionStatCards = computed(() => {
  const stats = extractionStatus.value?.stats;
  return [
    { label: t("synology.indexed"), value: stats?.indexed ?? "—" },
    { label: t("synology.pending"), value: stats?.pending ?? "—" },
    { label: t("synology.failed"), value: stats?.failed ?? "—" },
    { label: t("synology.skipped"), value: stats?.skipped ?? "—" },
    { label: t("synology.unsupported"), value: stats?.unsupported ?? "—" },
    { label: t("synology.missingContent"), value: stats?.missingContentRows ?? "—" },
    { label: t("synology.queuePending"), value: stats?.queuePending ?? "—" },
  ];
});
const connections = ref<SynologyConnection[]>([]);
const selectedId = ref<string | null>(null);
const policySources = ref<SourceSource[]>([]);
const policyShareId = ref<string | null>(null);
const policyModel = ref<"source_acl" | "qka_managed">("source_acl");
const policyManagedScopes = ref<SourceScope[]>([]);
const policySaving = ref(false);
const policyForm = ref({
  folderPrefix: "",
  ownerName: "",
  ownerEmail: "",
  approvalReference: "",
  notes: "",
  serviceReadVerified: false,
});
const creating = ref(false);
const connection = ref<SynologyConnection | null>(null);
const connectionOpen = ref(false);
const removing = ref(false);
const monitor = ref<SynologyMonitor | null>(null);
const inventory = ref<SynologyInventoryPage>({ items: [], totalCount: 0, page: 1, pageSize: 50 });
const search = ref("");
const fileType = ref<number | null>(null);
const form = ref({ name: "", sharedFolderPath: "", authMode: 1, username: "", password: "" });
const pagination = ref({ page: 1, rowsPerPage: 50, rowsNumber: 0 });
const previewOpen = ref(false);
const previewTitle = ref("");
const previewText = ref("");
let pollTimer: number | null = null;

const authOptions = computed(() => [
  { label: t("synology.authPassword"), value: 1 },
  { label: t("synology.authService"), value: 0 },
]);
const fileTypeOptions = computed(() => [
  { label: t("synology.typeExcel"), value: 1 },
  { label: t("synology.typePdf"), value: 2 },
  { label: t("synology.typeWord"), value: 3 },
  { label: t("synology.typePpt"), value: 4 },
  { label: t("synology.typeCsv"), value: 5 },
  { label: t("synology.typeOther"), value: 0 },
]);

const columns = computed<QTableColumn[]>(() => [
  { name: "select", label: "", field: "id", align: "left", style: "width: 36px" },
  { name: "fileName", label: t("synology.colName"), field: "fileName", align: "left", classes: "qka-col-name", headerClasses: "qka-col-name" },
  { name: "relativeFolderPath", label: t("synology.colFolder"), field: "relativeFolderPath", align: "left", classes: "qka-col-folder", headerClasses: "qka-col-folder" },
  { name: "extension", label: t("synology.colExt"), field: "extension", align: "left", classes: "qka-col-ext", headerClasses: "qka-col-ext" },
  { name: "sizeBytes", label: t("synology.colSize"), field: "sizeBytes", align: "right", classes: "qka-col-size", headerClasses: "qka-col-size", format: (value: number) => formatBytes(value) },
  { name: "lastWriteUtc", label: t("synology.colModified"), field: "lastWriteUtc", align: "left", classes: "qka-col-date", headerClasses: "qka-col-date", format: (value: string | null) => value ? new Date(value).toLocaleString() : "—" },
  { name: "syncStatus", label: t("synology.colSync"), field: "syncStatus", align: "left", classes: "qka-col-status", headerClasses: "qka-col-status", format: (value: number) => syncLabel(value) },
  { name: "indexingState", label: t("synology.colIndex"), field: "indexingState", align: "left", classes: "qka-col-status", headerClasses: "qka-col-status" },
  { name: "aclCaptureStatus", label: t("synology.colAcl"), field: "aclCaptureStatus", align: "left", classes: "qka-col-status", headerClasses: "qka-col-status", format: (value: number) => aclLabel(value) },
]);

const editingId = ref<string | null>(null);
const secretHint = computed(() =>
  connection.value?.serviceAccountSecretConfigured && !creating.value
    ? t("synology.passwordKeep")
    : t("synology.passwordRequired"),
);

function chipColor(status: number) {
  if (status === 1) return "positive";
  if (status === 2) return "negative";
  return "grey";
}
function chipLabel(status: number) {
  if (status === 1) return t("synology.statusConnected");
  if (status === 2) return t("synology.statusFailed");
  return t("synology.statusUnknown");
}
function applyConnection(row: SynologyConnection | null) {
  connection.value = row;
  form.value = {
    name: row?.name ?? "",
    sharedFolderPath: row?.configuredSharedFolderPath ?? "",
    authMode: row?.authMode ?? 1,
    username: row?.serviceAccountUserName ?? "",
    password: "",
  };
}
function isEditingRow(id: string) {
  return connectionOpen.value && !creating.value && editingId.value === id;
}
function closeForm() {
  connectionOpen.value = false;
  creating.value = false;
  editingId.value = null;
  testMessage.value = "";
  const row = connections.value.find((item) => item.id === selectedId.value) || null;
  applyConnection(row);
}
function toggleAdd() {
  if (creating.value && connectionOpen.value) {
    closeForm();
    return;
  }
  creating.value = true;
  editingId.value = null;
  connectionOpen.value = true;
  applyConnection(null);
  testMessage.value = "";
}
async function toggleEdit(id: string) {
  if (isEditingRow(id)) {
    closeForm();
    return;
  }
  creating.value = false;
  editingId.value = id;
  selectedId.value = id;
  connectionOpen.value = true;
  const row = connections.value.find((item) => item.id === id) || null;
  applyConnection(row);
  testMessage.value = "";
  await loadMonitor();
  await reloadInventory();
}
const jobBusy = computed(() => {
  const status = monitor.value?.currentJob?.status;
  return status === 0 || status === 1;
});
const statCards = computed(() => {
  const stats = monitor.value?.stats;
  return [
    { label: t("synology.totalFiles"), value: stats?.totalFiles ?? "—" },
    { label: t("synology.totalSize"), value: stats ? formatBytes(stats.totalBytes) : "—" },
    { label: t("synology.typeExcel"), value: stats?.excel ?? "—" },
    { label: t("synology.typePdf"), value: stats?.pdf ?? "—" },
    { label: t("synology.typeWord"), value: stats?.word ?? "—" },
    { label: t("synology.typePpt"), value: stats?.powerPoint ?? "—" },
    { label: t("synology.typeCsv"), value: stats?.csv ?? "—" },
    { label: t("synology.typeOther"), value: stats?.other ?? "—" },
    { label: t("synology.lastSync"), value: monitor.value?.lastSuccessfulSyncAt ? new Date(monitor.value.lastSuccessfulSyncAt).toLocaleString() : "—" },
    { label: t("synology.currentStatus"), value: monitor.value?.currentJob ? jobStatusLabel(monitor.value.currentJob.status) : t("synology.idle") },
    { label: t("synology.added"), value: stats?.new ?? "—" },
    { label: t("synology.updated"), value: stats?.modified ?? "—" },
    { label: t("synology.unchanged"), value: stats?.unchanged ?? "—" },
    { label: t("synology.removed"), value: stats?.removed ?? "—" },
  ];
});

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
function syncLabel(value: number) {
  return [t("synology.added"), t("synology.updated"), t("synology.unchanged"), t("synology.removed"), t("synology.failed")][value] ?? String(value);
}
function aclLabel(value: number) {
  return [t("synology.aclMissing"), t("synology.aclCaptured"), t("synology.aclDenied")][value] ?? String(value);
}
function jobStatusLabel(value: number) {
  return [t("synology.queued"), t("synology.scanning"), t("synology.completed"), t("synology.failed"), t("synology.cancelled")][value] ?? String(value);
}

async function loadConnections(preferId?: string | null) {
  connections.value = await api.synologyConnections();
  const preferred = preferId || selectedId.value;
  const match = connections.value.find((row) => row.id === preferred) || connections.value[0] || null;
  selectedId.value = match?.id ?? null;
  creating.value = false;
  editingId.value = null;
  connectionOpen.value = false;
  applyConnection(match);
}

async function selectConnection(id: string) {
  const keepEdit = isEditingRow(id);
  selectedId.value = id;
  creating.value = false;
  if (!keepEdit) {
    editingId.value = null;
    connectionOpen.value = false;
    const row = connections.value.find((item) => item.id === id) || null;
    applyConnection(row);
  }
  await loadMonitor();
  await reloadInventory();
}

async function loadMonitor() {
  if (!selectedId.value) {
    monitor.value = null;
    stopPoll();
    return;
  }
  monitor.value = await api.synologyMonitor(selectedId.value);
  if (monitor.value.connections?.length) {
    connections.value = monitor.value.connections;
  }
  if (monitor.value.connection) {
    connection.value = monitor.value.connection;
  }
  await loadExtractionStatus();
  if (jobBusy.value) startPoll();
  else stopPoll();
}

async function loadExtractionStatus() {
  if (!selectedId.value) {
    extractionStatus.value = null;
    return;
  }
  try {
    extractionStatus.value = await api.synologyExtractionStatus(selectedId.value);
  } catch {
    extractionStatus.value = null;
  }
}

function toggleInventorySelect(id: string, selected: boolean) {
  if (selected) {
    if (!selectedInventoryIds.value.includes(id)) selectedInventoryIds.value = [...selectedInventoryIds.value, id];
  } else {
    selectedInventoryIds.value = selectedInventoryIds.value.filter((item) => item !== id);
  }
}

async function runExtractMissing() {
  if (!selectedId.value) return;
  extractionBusy.value = true;
  extractionMessage.value = "";
  try {
    const batch = await api.synologyExtractMissing({
      connectionId: selectedId.value,
      forceOutsidePilot: false,
    });
    extractionOk.value = true;
    extractionMessage.value = `${t("synology.extractQueued")}: ${batch.queuedCount}`;
    await loadExtractionStatus();
  } catch (err) {
    extractionOk.value = false;
    extractionMessage.value = err instanceof Error ? err.message : t("synology.extractError");
  } finally {
    extractionBusy.value = false;
  }
}

async function runRetryFailed() {
  if (!selectedId.value) return;
  extractionBusy.value = true;
  extractionMessage.value = "";
  try {
    const batch = await api.synologyRetryFailed({ connectionId: selectedId.value });
    extractionOk.value = true;
    extractionMessage.value = `${t("synology.extractQueued")}: ${batch.queuedCount}`;
    await loadExtractionStatus();
  } catch (err) {
    extractionOk.value = false;
    extractionMessage.value = err instanceof Error ? err.message : t("synology.extractError");
  } finally {
    extractionBusy.value = false;
  }
}

async function runReindexSelected() {
  if (!selectedInventoryIds.value.length) return;
  extractionBusy.value = true;
  extractionMessage.value = "";
  try {
    const batch = await api.synologyReindexSelected({
      inventoryIds: selectedInventoryIds.value,
      forceOutsidePilot: true,
    });
    extractionOk.value = true;
    extractionMessage.value = `${t("synology.extractQueued")}: ${batch.queuedCount}`;
    selectedInventoryIds.value = [];
    await loadExtractionStatus();
  } catch (err) {
    extractionOk.value = false;
    extractionMessage.value = err instanceof Error ? err.message : t("synology.extractError");
  } finally {
    extractionBusy.value = false;
  }
}

async function runReindexFolder() {
  if (!selectedId.value || !reindexFolderPrefix.value.trim()) return;
  extractionBusy.value = true;
  extractionMessage.value = "";
  try {
    const batch = await api.synologyReindexFolder({
      connectionId: selectedId.value,
      folderPrefix: reindexFolderPrefix.value.trim(),
      forceOutsidePilot: true,
    });
    extractionOk.value = true;
    extractionMessage.value = `${t("synology.extractQueued")}: ${batch.queuedCount}`;
    await loadExtractionStatus();
  } catch (err) {
    extractionOk.value = false;
    extractionMessage.value = err instanceof Error ? err.message : t("synology.extractError");
  } finally {
    extractionBusy.value = false;
  }
}

async function loadInventory(page = pagination.value.page, pageSize = pagination.value.rowsPerPage) {
  if (!selectedId.value) {
    inventory.value = { items: [], totalCount: 0, page: 1, pageSize };
    pagination.value = { page: 1, rowsPerPage: pageSize, rowsNumber: 0 };
    return;
  }
  loadingInventory.value = true;
  try {
    inventory.value = await api.synologyInventory({
      search: search.value || undefined,
      fileType: fileType.value ?? undefined,
      page,
      pageSize,
      connectionId: selectedId.value,
    });
    pagination.value = {
      ...pagination.value,
      page: inventory.value.page,
      rowsPerPage: inventory.value.pageSize,
      rowsNumber: inventory.value.totalCount,
    };
  } finally {
    loadingInventory.value = false;
  }
}

async function reloadInventory() {
  await loadInventory(1, pagination.value.rowsPerPage);
}

async function onRequest(props: Parameters<NonNullable<QTableProps["onRequest"]>>[0]) {
  pagination.value = {
    page: props.pagination.page,
    rowsPerPage: props.pagination.rowsPerPage,
    rowsNumber: props.pagination.rowsNumber ?? pagination.value.rowsNumber,
  };
  await loadInventory(props.pagination.page, props.pagination.rowsPerPage);
}

async function save() {
  saving.value = true;
  error.value = "";
  try {
    const payload = {
      name: form.value.name,
      sharedFolderPath: form.value.sharedFolderPath,
      authMode: form.value.authMode,
      serviceAccountUserName: form.value.username,
      serviceAccountSecret: form.value.password.trim() ? form.value.password : undefined,
    };
    const saved = creating.value || !selectedId.value
      ? await api.createSynologyConnection(payload)
      : await api.saveSynologyConnection(payload, editingId.value || selectedId.value);
    form.value.password = "";
    await loadConnections(saved.id);
    await loadMonitor();
    await reloadInventory();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("synology.saveError");
  } finally {
    saving.value = false;
  }
}

async function removeSelected() {
  const id = editingId.value || selectedId.value;
  if (!id) return;
  removing.value = true;
  error.value = "";
  try {
    await api.removeSynologyConnection(id);
    await loadConnections(null);
    await loadMonitor();
    await reloadInventory();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("synology.removeError");
  } finally {
    removing.value = false;
  }
}

async function test() {
  testing.value = true;
  error.value = "";
  testMessage.value = "";
  try {
    let id = creating.value ? null : editingId.value || selectedId.value;
    if (creating.value || !id) {
      const payload = {
        name: form.value.name,
        sharedFolderPath: form.value.sharedFolderPath,
        authMode: form.value.authMode,
        serviceAccountUserName: form.value.username,
        serviceAccountSecret: form.value.password.trim() ? form.value.password : undefined,
      };
      const saved = await api.createSynologyConnection(payload);
      id = saved.id;
      form.value.password = "";
      connections.value = await api.synologyConnections();
      const row = connections.value.find((item) => item.id === id) || saved;
      selectedId.value = id;
      editingId.value = id;
      creating.value = false;
      connectionOpen.value = true;
      applyConnection(row);
    }
    const result = await api.testSynologyConnection(id);
    testOk.value = result.succeeded;
    testMessage.value = result.succeeded
      ? t("synology.testOk")
      : result.safeError || result.diagnostics.join(" ") || t("synology.testFail");
    connections.value = await api.synologyConnections();
    const row = connections.value.find((item) => item.id === id) || null;
    selectedId.value = id;
    editingId.value = id;
    creating.value = false;
    connectionOpen.value = true;
    applyConnection(row);
    await loadMonitor();
  } catch (err) {
    testOk.value = false;
    testMessage.value = err instanceof Error ? err.message : t("synology.testFail");
  } finally {
    testing.value = false;
  }
}

async function startSync() {
  if (!selectedId.value) return;
  starting.value = true;
  error.value = "";
  try {
    await api.startSynologySync(selectedId.value);
    await loadMonitor();
    startPoll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("synology.syncError");
  } finally {
    starting.value = false;
  }
}

async function cancel() {
  const jobId = monitor.value?.currentJob?.id;
  if (!jobId) return;
  cancelling.value = true;
  try {
    await api.cancelSynologyJob(jobId);
    await loadMonitor();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("synology.cancelError");
  } finally {
    cancelling.value = false;
  }
}

async function preview(id: string) {
  previewOpen.value = true;
  previewTitle.value = t("synology.preview");
  previewText.value = t("synology.previewLoading");
  try {
    const doc = await api.document(id);
    if (doc.denied) {
      previewText.value = doc.error || t("synology.previewDenied");
      return;
    }
    previewTitle.value = doc.title || t("synology.preview");
    previewText.value = doc.text || "—";
  } catch (err) {
    previewText.value = err instanceof Error ? err.message : t("synology.previewDenied");
  }
}

function startPoll() {
  if (pollTimer != null) return;
  pollTimer = window.setInterval(() => {
    void loadMonitor();
    void loadInventory(pagination.value.page, pagination.value.rowsPerPage);
  }, 2000);
}

function stopPoll() {
  if (pollTimer != null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

const policyShareOptions = computed(() =>
  policySources.value.map((s) => ({
    label: `${s.shareName}${s.isConfiguredPilotShare ? " (pilot)" : ""}`,
    value: s.approvedShareId,
  })),
);

async function loadAccessPolicy() {
  policySources.value = await api.sourceSources();
  if (!policyShareId.value) {
    const pilot = policySources.value.find((s) => s.isConfiguredPilotShare) || policySources.value[0];
    policyShareId.value = pilot?.approvedShareId || null;
  }
  onPolicyShareSelected();
}

function onPolicyShareSelected() {
  const share = policySources.value.find((s) => s.approvedShareId === policyShareId.value);
  policyManagedScopes.value = (share?.scopes || []).filter(
    (s) => s.trustClass === "corporate_designated" && s.status === "active" && s.serviceReadVerified && !!s.approvalReference,
  );
  policyModel.value = policyManagedScopes.value.length ? "qka_managed" : "source_acl";
  if (!policyForm.value.folderPrefix && share?.pilotFolderPrefix) {
    policyForm.value.folderPrefix = share.pilotFolderPrefix;
  }
}

async function activateQkaManaged() {
  if (!policyShareId.value) return;
  policySaving.value = true;
  error.value = "";
  try {
    await api.registerCorporateScope({
      approvedShareId: policyShareId.value,
      folderPrefix: policyForm.value.folderPrefix,
      displayName: policyForm.value.folderPrefix,
      sourceOwnerName: policyForm.value.ownerName,
      sourceOwnerEmail: policyForm.value.ownerEmail,
      approvalReference: policyForm.value.approvalReference,
      approvalNotes: policyForm.value.notes,
      serviceReadVerified: policyForm.value.serviceReadVerified,
    });
    policyForm.value = {
      ...policyForm.value,
      approvalReference: "",
      notes: "",
      serviceReadVerified: false,
    };
    await loadAccessPolicy();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("synology.policySaveError");
  } finally {
    policySaving.value = false;
  }
}

async function revokePolicyScope(scopeId: string) {
  policySaving.value = true;
  error.value = "";
  try {
    await api.revokeSourceScope(scopeId);
    await loadAccessPolicy();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("synology.policySaveError");
  } finally {
    policySaving.value = false;
  }
}

onMounted(async () => {
  if (!session.user?.superAdmin) return;
  try {
    await loadConnections();
    await loadMonitor();
    await loadInventory();
    await loadAccessPolicy();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("synology.loadError");
  }
});
onUnmounted(stopPoll);
</script>

<style scoped>
.qka-synology {
  max-width: 100%;
  min-width: 0;
  overflow-x: hidden;
}

.qka-synology-stack {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  min-width: 0;
}

.qka-inline-form {
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.02);
  min-width: 0;
}

.body--dark .qka-inline-form {
  border-color: rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.04);
}

.qka-inventory {
  max-width: 100%;
}

.qka-inventory :deep(.q-table__container),
.qka-inventory :deep(.q-table__middle) {
  max-width: 100%;
  overflow-x: hidden;
}

.qka-inventory :deep(table) {
  table-layout: fixed;
  width: 100%;
}

.qka-inventory :deep(th),
.qka-inventory :deep(td) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}

.qka-col-name {
  width: 28%;
}

.qka-col-folder {
  width: 22%;
}

.qka-col-ext {
  width: 7%;
}

.qka-col-size {
  width: 8%;
}

.qka-col-date {
  width: 14%;
}

.qka-col-status {
  width: 7%;
}

.qka-ellipsis-cell {
  max-width: 0;
}

.qka-ellipsis,
.qka-file-link {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.qka-file-link {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--q-primary);
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.qka-preview {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: Consolas, "Courier New", monospace;
  font-size: 0.85rem;
  max-height: 420px;
  overflow: auto;
}
</style>
