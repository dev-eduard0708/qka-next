<template>
  <q-page padding>
    <h1 class="text-h5 q-mb-xs">{{ t("access.title") }}</h1>
    <p class="text-body2 text-grey-7 q-mb-md">{{ t("access.body") }}</p>

    <template v-if="session.user?.superAdmin">
      <q-tabs v-model="tab" dense class="text-primary q-mb-md" align="left" active-color="primary" indicator-color="primary">
        <q-tab name="emails" :label="t('access.tabEmails')" />
        <q-tab name="source" :label="t('access.tabSource')" />
      </q-tabs>

      <q-tab-panels v-model="tab" animated>
        <q-tab-panel name="emails" class="q-pa-none">
          <q-card bordered flat class="qka-card">
            <q-card-section>
              <div class="text-subtitle1 q-mb-sm">{{ t("access.allowlist") }}</div>
              <p class="text-caption q-mb-sm">{{ t("access.domainHint") }}</p>
              <p class="text-caption text-warning q-mb-md">{{ t("access.allowlistNoDocs") }}</p>
              <q-banner v-if="emailError" dense class="bg-negative text-white q-mb-md">{{ emailError }}</q-banner>
              <q-form class="row q-col-gutter-sm q-mb-md" @submit.prevent="addEmail">
                <div class="col">
                  <q-input v-model="email" outlined dense :label="t('access.email')" type="email" />
                </div>
                <div class="col-auto">
                  <q-btn color="primary" unelevated type="submit" :loading="addingEmail" :label="t('access.add')" />
                </div>
              </q-form>
              <q-list v-if="emails.length" bordered separator>
                <q-item v-for="row in emails" :key="row.id">
                  <q-item-section>
                    <q-item-label>{{ row.email }}</q-item-label>
                    <q-item-label caption>{{ row.enabled ? t("access.enabled") : t("access.disabled") }}</q-item-label>
                  </q-item-section>
                  <q-item-section side>
                    <div class="row items-center no-wrap q-gutter-sm">
                      <q-toggle
                        :model-value="row.enabled"
                        :disable="busyEmailId === row.id"
                        @update:model-value="(value: boolean) => toggleEmail(row, value)"
                      />
                      <q-btn flat dense color="negative" :label="t('access.remove')" :loading="busyEmailId === row.id" @click="removeEmail(row.id)" />
                    </div>
                  </q-item-section>
                </q-item>
              </q-list>
              <div v-else class="text-body2">{{ t("access.empty") }}</div>
            </q-card-section>
          </q-card>
        </q-tab-panel>

        <q-tab-panel name="source" class="q-pa-none">
          <q-banner v-if="sourceError" dense rounded class="bg-negative text-white q-mb-md">{{ sourceError }}</q-banner>

          <div class="row q-col-gutter-md">
            <div class="col-12 col-md-4">
              <q-card bordered flat class="qka-card q-mb-md">
                <q-card-section>
                  <div class="text-subtitle1 q-mb-sm">{{ t("access.employees") }}</div>
                  <q-select
                    v-model="selectedAllowlistId"
                    outlined
                    dense
                    emit-value
                    map-options
                    :options="employeeOptions"
                    :label="t('access.selectEmployee')"
                    clearable
                    @update:model-value="onUserSelected"
                  />
                  <div v-if="selectedEmployee" class="q-mt-md">
                    <q-badge :color="identityBadgeColor(selectedEmployee.identityStatus)" :label="identityStatusLabel(selectedEmployee.identityStatus)" />
                    <div v-if="selectedEmployee.identityStatus === 'pending'" class="text-caption text-warning q-mt-sm">{{ t("access.pendingHint") }}</div>
                    <div v-if="selectedEmployee.identityStatus === 'disabled'" class="text-caption text-negative q-mt-sm">{{ t("access.disabledHint") }}</div>
                  </div>
                </q-card-section>
              </q-card>

              <q-card bordered flat class="qka-card">
                <q-card-section>
                  <div class="text-subtitle2 q-mb-sm">{{ t("access.identitySummary") }}</div>
                  <div v-if="selectedEmployee" class="text-caption qka-kv">
                    <div><span class="text-grey-7">{{ t("access.googleSub") }}</span><br />{{ selectedEmployee.googleSub || t("access.googleSubMissing") }}</div>
                    <div class="q-mt-sm">
                      <span class="text-grey-7">{{ t("access.adStatus") }}</span><br />
                      {{
                        selectedEmployee.adIdentities.length
                          ? selectedEmployee.adIdentities.map((a) => a.domainUsername || `…${a.sidSuffix}`).join(", ")
                          : t("access.noAdLinked")
                      }}
                    </div>
                    <div class="q-mt-sm">
                      <span class="text-grey-7">{{ t("access.sourcePermission") }}</span><br />
                      {{ effective?.sampleDocument?.sourcePermission || "—" }}
                    </div>
                    <div class="q-mt-sm">
                      <span class="text-grey-7">{{ t("access.effective") }}</span><br />
                      <q-badge
                        :color="effective?.sampleDocument?.allowed ? 'positive' : 'negative'"
                        :label="(effective?.sampleDocument?.effectiveAccess || 'deny').toUpperCase()"
                      />
                      <div v-if="effective?.sampleDocument?.denialReason" class="q-mt-xs text-grey-7">
                        {{ effective.sampleDocument.denialReason }}
                      </div>
                    </div>
                  </div>
                  <div v-else class="text-caption text-grey-7">{{ t("access.effectiveEmpty") }}</div>
                </q-card-section>
              </q-card>
            </div>

            <div class="col-12 col-md-8">
              <q-card bordered flat class="qka-card">
                <q-card-section>
                  <div class="text-subtitle1 q-mb-md">{{ t("access.ruleCard") }}</div>

                  <q-select
                    v-model="selectedShareId"
                    outlined
                    dense
                    emit-value
                    map-options
                    :options="shareOptions"
                    :label="t('access.selectSource')"
                    class="q-mb-md"
                    @update:model-value="onShareSelected"
                  />

                  <div v-if="eligibility && !eligibility.eligible" class="q-mb-md">
                    <q-banner dense rounded class="bg-warning text-dark">
                      {{ eligibility.reason }}
                      <template #action>
                        <q-btn flat dense color="primary" :to="{ name: 'synology' }" :label="t('access.openSourceSettings')" />
                      </template>
                    </q-banner>
                  </div>

                  <div class="text-subtitle2 q-mb-sm">{{ t("access.accessScope") }}</div>
                  <q-btn-toggle
                    v-model="pathMode"
                    spread
                    no-caps
                    unelevated
                    toggle-color="primary"
                    class="qka-scope-toggle q-mb-md full-width"
                    :options="[
                      { label: t('access.scopeAll'), value: 'all' },
                      { label: t('access.scopeSelected'), value: 'selected' },
                    ]"
                  />

                  <template v-if="pathMode === 'selected'">
                    <q-checkbox v-model="includeSubfolders" dense :label="t('access.includeSubfolders')" class="q-mb-sm" />
                    <q-tree
                      v-if="folderNodes.length"
                      :nodes="folderNodes"
                      node-key="id"
                      tick-strategy="strict"
                      v-model:ticked="tickedPaths"
                      default-expand-all
                      class="q-mb-md qka-tree"
                    >
                      <template #default-header="prop">
                        <div class="row items-center no-wrap">
                          <q-icon :name="prop.node.icon || (prop.node.isFile ? 'insert_drive_file' : 'folder')" size="18px" class="q-mr-sm" />
                          <span>{{ prop.node.label }}</span>
                        </div>
                      </template>
                    </q-tree>
                    <div v-else class="text-caption text-grey-7 q-mb-md">{{ t("access.noFolders") }}</div>
                  </template>

                  <div class="text-subtitle2 q-mb-sm">{{ t("access.fileTypes") }}</div>
                  <q-checkbox
                    dense
                    class="q-mb-sm"
                    :model-value="allFileTypesSelected"
                    :indeterminate="someFileTypesSelected"
                    :label="t('access.selectAllFileTypes')"
                    @update:model-value="toggleAllFileTypes"
                  />
                  <div class="row q-col-gutter-sm q-mb-md">
                    <div v-for="ft in fileTypeOptions" :key="ft.value" class="col-auto">
                      <q-checkbox v-model="fileTypes" :val="ft.value" dense :label="ft.label">
                        <template #default>
                          <div class="row items-center no-wrap">
                            <q-icon :name="ft.icon" size="18px" class="q-mr-xs" />
                            <span>{{ ft.label }}</span>
                          </div>
                        </template>
                      </q-checkbox>
                    </div>
                  </div>
                  <div class="text-caption text-grey-7 q-mb-md">{{ t("access.fileTypesHint") }}</div>

                  <div class="text-subtitle2 q-mb-sm">{{ t("access.permissions") }}</div>
                  <div class="row q-gutter-md q-mb-md">
                    <q-checkbox v-model="capRead" dense :label="t('access.capRead')" />
                    <q-checkbox v-model="capSearch" dense :label="t('access.capSearch')" />
                    <q-checkbox v-model="capPreview" dense :label="t('access.capPreview')" />
                  </div>

                  <q-banner dense rounded class="bg-blue-1 text-dark q-mb-md">
                    <div class="text-caption">{{ t("access.configuredRule") }}: {{ ruleSummary }}</div>
                    <div class="text-caption q-mt-xs">
                      {{ t("access.effective") }}:
                      {{
                        effective?.sampleDocument?.allowed
                          ? t("access.effectiveAllowConfigured")
                          : effective?.sampleDocument?.denialReason || t("access.effectiveDenySource")
                      }}
                    </div>
                  </q-banner>

                  <div class="row justify-end">
                    <q-btn
                      color="primary"
                      unelevated
                      no-caps
                      :loading="granting"
                      :disable="!canSaveRule"
                      :label="t('access.saveRule')"
                      @click="saveRule"
                    />
                  </div>
                  <div v-if="selectedEmployee && !selectedEmployee.canGrant" class="text-caption text-warning q-mt-sm">
                    {{ t("access.grantRequiresLinked") }}
                  </div>
                </q-card-section>
              </q-card>
            </div>
          </div>

          <q-card bordered flat class="qka-card q-mt-md">
            <q-card-section>
              <div class="text-subtitle1 q-mb-md">{{ t("access.audit") }}</div>
              <div class="row q-col-gutter-sm q-mb-md">
                <div class="col-12 col-sm-4">
                  <q-input v-model="auditSearch" outlined dense clearable :label="t('access.auditSearch')" @keyup.enter="loadAudit" />
                </div>
                <div class="col-12 col-sm-3">
                  <q-select v-model="auditAction" outlined dense clearable emit-value map-options :options="auditActionOptions" :label="t('access.auditAction')" />
                </div>
                <div class="col-auto">
                  <q-btn color="primary" outline unelevated :label="t('access.auditApply')" @click="loadAudit" />
                </div>
              </div>
              <q-table
                flat
                bordered
                dense
                row-key="id"
                :rows="auditRows"
                :columns="auditColumns"
                :loading="auditLoading"
                v-model:pagination="auditPagination"
                :rows-per-page-options="[10, 20, 50]"
                @request="onAuditRequest"
              >
                <template #body="props">
                  <q-tr :props="props" class="cursor-pointer" @click="props.expand = !props.expand">
                    <q-td key="createdAt" :props="props">{{ formatWhen(props.row.createdAt) }}</q-td>
                    <q-td key="employeeEmail" :props="props">{{ props.row.employeeEmail || "—" }}</q-td>
                    <q-td key="shareName" :props="props">{{ props.row.shareName || "—" }}</q-td>
                    <q-td key="scope" :props="props">{{ props.row.scope || "—" }}</q-td>
                    <q-td key="fileTypes" :props="props">{{ (props.row.fileTypes || []).join(", ") || "all" }}</q-td>
                    <q-td key="action" :props="props">
                      <q-badge outline :label="props.row.action" />
                    </q-td>
                    <q-td key="changedBy" :props="props">{{ props.row.changedBy }}</q-td>
                    <q-td key="detail" :props="props">{{ props.row.detail }}</q-td>
                  </q-tr>
                  <q-tr v-if="props.expand" :props="props">
                    <q-td colspan="100%">
                      <div class="row q-col-gutter-md q-pa-sm text-caption">
                        <div class="col-12 col-md-6">
                          <div class="text-weight-medium q-mb-xs">{{ t("access.auditBefore") }}</div>
                          <pre class="qka-pre">{{ JSON.stringify(props.row.before || {}, null, 2) }}</pre>
                        </div>
                        <div class="col-12 col-md-6">
                          <div class="text-weight-medium q-mb-xs">{{ t("access.auditAfter") }}</div>
                          <pre class="qka-pre">{{ JSON.stringify(props.row.after || {}, null, 2) }}</pre>
                        </div>
                      </div>
                    </q-td>
                  </q-tr>
                </template>
              </q-table>
            </q-card-section>
          </q-card>
        </q-tab-panel>
      </q-tab-panels>
    </template>
    <q-banner v-else dense class="bg-warning text-dark">{{ t("access.adminOnly") }}</q-banner>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { QTableProps } from "quasar";
import {
  api,
  type AllowedEmail,
  type SourceAuditEntry,
  type SourceEffective,
  type SourceEmployee,
  type SourceFolderNode,
  type SourceScope,
  type SourceSource,
} from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const tab = ref("emails");

const emails = ref<AllowedEmail[]>([]);
const email = ref("");
const emailError = ref("");
const addingEmail = ref(false);
const busyEmailId = ref<string | null>(null);

const sourceError = ref("");
const employees = ref<SourceEmployee[]>([]);
const sources = ref<SourceSource[]>([]);
const folderNodes = ref<SourceFolderNode[]>([]);
const tickedPaths = ref<string[]>([]);
const selectedAllowlistId = ref<string | null>(null);
const selectedShareId = ref<string | null>(null);
const selectedScopeId = ref<string | null>(null);
const eligibility = ref<{ eligible: boolean; scopes: SourceScope[]; reason: string | null } | null>(null);
const effective = ref<SourceEffective | null>(null);
const pathMode = ref<"all" | "selected">("all");
const includeSubfolders = ref(true);
const fileTypes = ref<string[]>(["excel", "word", "pdf", "powerpoint", "images", "csv", "text", "other"]);
const capRead = ref(true);
const capSearch = ref(true);
const capPreview = ref(true);
const granting = ref(false);

const auditRows = ref<SourceAuditEntry[]>([]);
const auditLoading = ref(false);
const auditSearch = ref("");
const auditAction = ref<string | null>(null);
const auditPagination = ref({ page: 1, rowsPerPage: 20, rowsNumber: 0 });

const fileTypeOptions = [
  { value: "excel", label: "Excel", icon: "grid_on" },
  { value: "word", label: "Word", icon: "description" },
  { value: "pdf", label: "PDF", icon: "picture_as_pdf" },
  { value: "powerpoint", label: "PowerPoint", icon: "slideshow" },
  { value: "images", label: "Images", icon: "image" },
  { value: "csv", label: "CSV", icon: "table_chart" },
  { value: "text", label: "Text", icon: "article" },
  { value: "other", label: "Other", icon: "insert_drive_file" },
];

const auditActionOptions = [
  { label: "Created", value: "Created" },
  { label: "Updated", value: "Updated" },
  { label: "Disabled", value: "Disabled" },
  { label: "Revoked", value: "Revoked" },
];

const auditColumns: QTableProps["columns"] = [
  { name: "createdAt", label: "Date / Time", field: "createdAt", align: "left" },
  { name: "employeeEmail", label: "Employee", field: "employeeEmail", align: "left" },
  { name: "shareName", label: "Data Source", field: "shareName", align: "left" },
  { name: "scope", label: "Scope", field: "scope", align: "left" },
  { name: "fileTypes", label: "File Types", field: "fileTypes", align: "left" },
  { name: "action", label: "Action", field: "action", align: "left" },
  { name: "changedBy", label: "Changed By", field: "changedBy", align: "left" },
  { name: "detail", label: "Details", field: "detail", align: "left" },
];

const employeeOptions = computed(() =>
  employees.value.map((e) => ({
    label: `${e.primaryEmail} — ${identityStatusLabel(e.identityStatus)}`,
    value: e.allowlistId,
  })),
);
const shareOptions = computed(() =>
  sources.value.map((s) => ({
    label: `${s.shareName}${s.isConfiguredPilotShare ? " (pilot)" : ""} — ${s.inventoryCount} files`,
    value: s.approvedShareId,
  })),
);
const selectedEmployee = computed(() => employees.value.find((e) => e.allowlistId === selectedAllowlistId.value) || null);
const selectedShare = computed(() => sources.value.find((s) => s.approvedShareId === selectedShareId.value) || null);

const allFileTypeValues = computed(() => fileTypeOptions.map((ft) => ft.value));
const allFileTypesSelected = computed(
  () =>
    allFileTypeValues.value.length > 0 &&
    allFileTypeValues.value.every((value) => fileTypes.value.includes(value)),
);
const someFileTypesSelected = computed(
  () => fileTypes.value.length > 0 && !allFileTypesSelected.value,
);

function toggleAllFileTypes(checked: boolean | null) {
  fileTypes.value = checked ? [...allFileTypeValues.value] : [];
}

const ruleSummary = computed(() => {
  const parts: string[] = [];
  parts.push(pathMode.value === "all" ? t("access.scopeAll") : t("access.scopeSelected"));
  if (pathMode.value === "selected" && includeSubfolders.value) parts.push(t("access.includeSubfolders"));
  if (!fileTypes.value.length || allFileTypesSelected.value) parts.push(t("access.allFileTypes"));
  else parts.push(fileTypes.value.map((v) => fileTypeOptions.find((f) => f.value === v)?.label || v).join(" + "));
  const caps = [
    capRead.value ? t("access.capRead") : null,
    capSearch.value ? t("access.capSearch") : null,
    capPreview.value ? t("access.capPreview") : null,
  ].filter(Boolean);
  if (caps.length) parts.push(caps.join(", "));
  return parts.join(" · ");
});

const canSaveRule = computed(() => {
  if (!selectedEmployee?.canGrant || !selectedScopeId.value || !eligibility?.value?.eligible) return false;
  if (!(capRead.value || capSearch.value || capPreview.value)) return false;
  if (pathMode.value === "selected" && !tickedPaths.value.length) return false;
  return true;
});

function identityStatusLabel(status: SourceEmployee["identityStatus"]) {
  if (status === "linked") return t("access.statusLinked");
  if (status === "pending") return t("access.statusPending");
  return t("access.statusDisabled");
}
function identityBadgeColor(status: SourceEmployee["identityStatus"]) {
  if (status === "linked") return "positive";
  if (status === "pending") return "warning";
  return "negative";
}
function formatWhen(value: string | null) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

async function loadEmails() {
  if (!session.user?.superAdmin) {
    emails.value = [];
    return;
  }
  try {
    emails.value = await api.listAllowedEmails();
    emailError.value = "";
  } catch (err) {
    emailError.value = err instanceof Error ? err.message : t("access.loadError");
  }
}

async function addEmail() {
  addingEmail.value = true;
  emailError.value = "";
  try {
    const created = await api.addAllowedEmail(email.value);
    emails.value = [...emails.value.filter((row) => row.id !== created.id), created].sort((a, b) => a.email.localeCompare(b.email));
    email.value = "";
  } catch (err) {
    emailError.value = err instanceof Error ? err.message : t("access.addError");
  } finally {
    addingEmail.value = false;
  }
}

async function toggleEmail(row: AllowedEmail, enabled: boolean) {
  busyEmailId.value = row.id;
  try {
    const updated = await api.setAllowedEmailEnabled(row.id, enabled);
    emails.value = emails.value.map((item) => (item.id === updated.id ? updated : item));
  } catch (err) {
    emailError.value = err instanceof Error ? err.message : t("access.updateError");
  } finally {
    busyEmailId.value = null;
  }
}

async function removeEmail(id: string) {
  busyEmailId.value = id;
  try {
    await api.removeAllowedEmail(id);
    emails.value = emails.value.filter((row) => row.id !== id);
  } catch (err) {
    emailError.value = err instanceof Error ? err.message : t("access.removeError");
  } finally {
    busyEmailId.value = null;
  }
}

async function loadSourcePanel() {
  if (!session.user?.superAdmin) return;
  sourceError.value = "";
  try {
    const [emps, srcs] = await Promise.all([api.sourceEmployees(), api.sourceSources()]);
    employees.value = emps;
    sources.value = srcs;
    if (!selectedShareId.value) {
      const pilot = srcs.find((s) => s.isConfiguredPilotShare) || srcs[0];
      if (pilot) {
        selectedShareId.value = pilot.approvedShareId;
        await onShareSelected();
      }
    }
    await loadAudit();
  } catch (err) {
    sourceError.value = err instanceof Error ? err.message : t("access.sourceLoadError");
  }
}

async function onShareSelected() {
  folderNodes.value = [];
  tickedPaths.value = [];
  selectedScopeId.value = null;
  eligibility.value = null;
  if (!selectedShareId.value) return;
  try {
    eligibility.value = await api.sourceEligibleScopes(selectedShareId.value);
    selectedScopeId.value = eligibility.value.scopes[0]?.id || null;
    // Prefer corporate scope prefix; otherwise show the full share folder tree
    // (pilot-only EDUARDTESTCLOUD has just a few files and looks like "one folder").
    const scopePrefix = eligibility.value.scopes[0]?.folderPrefixNormalized;
    const rootPrefix = scopePrefix && scopePrefix.trim() ? scopePrefix : "";
    folderNodes.value = await api.sourceFolderTree(selectedShareId.value, rootPrefix);
  } catch (err) {
    sourceError.value = err instanceof Error ? err.message : t("access.sourceLoadError");
  }
}

async function onUserSelected() {
  effective.value = null;
  const employee = selectedEmployee.value;
  if (!employee?.qkaUserId || employee.identityStatus !== "linked") return;
  try {
    effective.value = await api.sourceEffective(employee.qkaUserId);
    const grant = effective.value.grants.find((g) => g.status === "active");
    if (grant) {
      pathMode.value = grant.pathMode === "selected" ? "selected" : "all";
      includeSubfolders.value = grant.includeSubfolders;
      tickedPaths.value = [...(grant.selectedPaths || [])];
      fileTypes.value = grant.fileTypes?.length ? [...grant.fileTypes] : [...allFileTypeValues.value];
      capRead.value = grant.capabilityRead;
      capSearch.value = grant.capabilitySearch;
      capPreview.value = grant.capabilityPreview;
      if (grant.scopeId) selectedScopeId.value = grant.scopeId;
    }
  } catch (err) {
    sourceError.value = err instanceof Error ? err.message : t("access.sourceLoadError");
  }
}

async function saveRule() {
  const employee = selectedEmployee.value;
  if (!employee?.canGrant || !employee.qkaUserId || !selectedScopeId.value) return;
  granting.value = true;
  sourceError.value = "";
  try {
    await api.upsertSourceGrant({
      scopeId: selectedScopeId.value,
      qkaUserId: employee.qkaUserId,
      capabilityRead: capRead.value,
      capabilitySearch: capSearch.value,
      capabilityPreview: capPreview.value,
      pathMode: pathMode.value,
      includeSubfolders: includeSubfolders.value,
      selectedPaths: pathMode.value === "selected" ? tickedPaths.value : [],
      fileTypes: allFileTypesSelected.value ? [] : fileTypes.value,
      reason: "access-rule",
    });
    await onUserSelected();
    await loadAudit();
  } catch (err) {
    sourceError.value = err instanceof Error ? err.message : t("access.grantError");
  } finally {
    granting.value = false;
  }
}

async function loadAudit() {
  auditLoading.value = true;
  try {
    const page = await api.sourceAudit({
      search: auditSearch.value || undefined,
      action: auditAction.value || undefined,
      page: auditPagination.value.page,
      pageSize: auditPagination.value.rowsPerPage,
    });
    auditRows.value = page.items;
    auditPagination.value = {
      ...auditPagination.value,
      rowsNumber: page.total,
      page: page.page,
      rowsPerPage: page.pageSize,
    };
  } catch (err) {
    sourceError.value = err instanceof Error ? err.message : t("access.sourceLoadError");
  } finally {
    auditLoading.value = false;
  }
}

async function onAuditRequest(req: { pagination: { page: number; rowsPerPage: number } }) {
  auditPagination.value.page = req.pagination.page;
  auditPagination.value.rowsPerPage = req.pagination.rowsPerPage;
  await loadAudit();
}

watch(tab, (value) => {
  if (value === "source") void loadSourcePanel();
});

onMounted(async () => {
  await loadEmails();
});
</script>

<style scoped>
.qka-card {
  border-radius: 12px;
}
.qka-scope-toggle {
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 10px;
  overflow: hidden;
}
.qka-tree {
  max-height: 280px;
  overflow: auto;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  padding: 8px;
}
.qka-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  background: rgba(0, 0, 0, 0.03);
  border-radius: 8px;
  padding: 8px;
}
.body--dark .qka-scope-toggle {
  border-color: rgba(255, 255, 255, 0.18);
}
.body--dark .qka-tree {
  border-color: rgba(255, 255, 255, 0.12);
}
.body--dark .qka-pre {
  background: rgba(255, 255, 255, 0.06);
}
</style>
