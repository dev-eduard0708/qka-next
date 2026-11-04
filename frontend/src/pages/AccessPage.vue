<template>
  <q-page padding>
    <h1 class="text-h5 q-mb-xs">{{ t("access.title") }}</h1>
    <p class="text-body2 text-grey-7 q-mb-md">{{ t("access.body") }}</p>

    <template v-if="session.user?.superAdmin">
      <q-tabs v-model="tab" dense class="text-primary q-mb-md" align="left" active-color="primary" indicator-color="primary">
        <q-tab name="users" :label="t('access.tabUsers')" />
        <q-tab name="source" :label="t('access.tabSource')" />
        <q-tab name="audit" :label="t('access.tabAudit')" />
      </q-tabs>

      <q-tab-panels v-model="tab" animated>
        <q-tab-panel name="users" class="q-pa-none">
          <q-banner v-if="usersError" dense rounded class="bg-negative text-white q-mb-md">{{ usersError }}</q-banner>
          <q-card bordered flat class="qka-card">
            <q-card-section>
              <div class="row items-center q-col-gutter-sm q-mb-md">
                <div class="col-12 col-md-3">
                  <q-input v-model="userSearch" outlined dense clearable debounce="300" :label="t('access.userSearch')" @update:model-value="reloadUsers" />
                </div>
                <div class="col-6 col-md-2">
                  <q-select v-model="filterAccount" outlined dense clearable emit-value map-options :options="accountFilterOptions" :label="t('access.filterAccount')" @update:model-value="reloadUsers" />
                </div>
                <div class="col-6 col-md-2">
                  <q-select v-model="filterIdentity" outlined dense clearable emit-value map-options :options="identityFilterOptions" :label="t('access.filterIdentity')" @update:model-value="reloadUsers" />
                </div>
                <div class="col-6 col-md-2">
                  <q-select v-model="filterMembership" outlined dense clearable emit-value map-options :options="membershipFilterOptions" :label="t('access.filterMembership')" @update:model-value="reloadUsers" />
                </div>
                <div class="col-auto">
                  <q-btn color="primary" unelevated no-caps :label="t('access.addUser')" @click="openAddUser" />
                </div>
                <div class="col-auto">
                  <q-btn outline color="primary" no-caps :label="t('access.importMembers')" @click="importDialog = true" />
                </div>
              </div>
              <p class="text-caption text-grey-7 q-mb-sm">{{ membershipNote }}</p>
              <q-table
                flat
                bordered
                dense
                :row-key="(row: DirectoryUser) => row.allowlistId || row.qkaUserId || row.primaryEmail"
                :rows="userRows"
                :columns="userColumns"
                :loading="usersLoading"
                v-model:pagination="userPagination"
                :rows-per-page-options="[10, 25, 50]"
                @request="onUsersRequest"
              >
                <template #body-cell-displayName="props">
                  <q-td :props="props">{{ props.row.displayName }}</q-td>
                </template>
                <template #body-cell-domainUsername="props">
                  <q-td :props="props">{{ props.row.domainUsername || "—" }}</q-td>
                </template>
                <template #body-cell-primaryEmail="props">
                  <q-td :props="props">{{ props.row.primaryEmail || "—" }}</q-td>
                </template>
                <template #body-cell-department="props">
                  <q-td :props="props">{{ props.row.department || "—" }}</q-td>
                </template>
                <template #body-cell-identitySource="props">
                  <q-td :props="props">
                    <q-badge outline color="primary" :label="identitySourceLabel(props.row.identitySource)" />
                  </q-td>
                </template>
                <template #body-cell-localChatEnabled="props">
                  <q-td :props="props">
                    <q-badge :color="props.row.localChatEnabled ? 'positive' : 'grey'" :label="props.row.localChatEnabled ? t('access.on') : t('access.off')" />
                  </q-td>
                </template>
                <template #body-cell-mcpEnabled="props">
                  <q-td :props="props">
                    <q-badge :color="props.row.mcpEnabled ? 'positive' : 'grey'" :label="props.row.mcpEnabled ? t('access.on') : t('access.off')" />
                  </q-td>
                </template>
                <template #body-cell-accountStatus="props">
                  <q-td :props="props">
                    <q-badge :color="accountBadgeColor(props.row.accountStatus)" :label="accountStatusLabel(props.row.accountStatus)" />
                  </q-td>
                </template>
                <template #body-cell-actions="props">
                  <q-td :props="props">
                    <div class="row no-wrap q-gutter-xs">
                      <q-btn flat dense size="sm" color="primary" no-caps :label="t('access.edit')" @click="openEditUser(props.row)" />
                      <q-btn
                        flat
                        dense
                        size="sm"
                        :color="props.row.accountStatus === 'disabled' ? 'positive' : 'negative'"
                        no-caps
                        :loading="busyUserId === (props.row.allowlistId || props.row.qkaUserId)"
                        :label="props.row.accountStatus === 'disabled' ? t('access.enable') : t('access.disable')"
                        @click="toggleUserEnabled(props.row)"
                      />
                    </div>
                  </q-td>
                </template>
              </q-table>
            </q-card-section>
          </q-card>

          <q-dialog v-model="userDialog">
            <q-card style="min-width: 480px; max-width: 94vw">
              <q-card-section>
                <div class="text-subtitle1">{{ editingUser ? t("access.editUser") : t("access.addUser") }}</div>
                <p class="text-caption text-grey-7">
                  {{ editingUser ? t("access.domainHint") : t("access.identitySourceHint") }}
                </p>
              </q-card-section>
              <q-card-section class="q-gutter-md">
                <q-btn-toggle
                  v-if="!editingUser"
                  v-model="userForm.identitySource"
                  spread
                  unelevated
                  toggle-color="primary"
                  :options="identitySourceOptions"
                  @update:model-value="onIdentitySourceChange"
                />

                <template v-if="!editingUser && userForm.identitySource === 'active_directory'">
                  <q-select
                    v-model="selectedAdUser"
                    outlined
                    dense
                    use-input
                    fill-input
                    hide-selected
                    input-debounce="300"
                    :options="adPickerOptions"
                    :loading="adPickerLoading"
                    option-value="objectGuid"
                    option-label="label"
                    emit-value
                    map-options
                    :label="t('access.adPicker')"
                    @filter="filterAdUsers"
                    @update:model-value="onAdUserSelected"
                  >
                    <template #option="scope">
                      <q-item v-bind="scope.itemProps">
                        <q-item-section>
                          <q-item-label>{{ scope.opt.displayName }}</q-item-label>
                          <q-item-label caption>
                            {{ scope.opt.samAccountName }}
                            ·
                            {{ scope.opt.department || t("access.adDepartmentNotSet") }}
                          </q-item-label>
                        </q-item-section>
                      </q-item>
                    </template>
                  </q-select>
                  <q-input v-model="userForm.displayName" outlined dense :label="t('access.fullName')" readonly />
                  <q-input v-model="userForm.domainUsername" outlined dense :label="t('access.domainUsername')" readonly />
                  <q-banner v-if="adDepartmentBlank" dense class="bg-warning text-dark">
                    {{ t("access.adDepartmentBlank") }}
                  </q-banner>
                  <q-input
                    v-model="userForm.departmentOverride"
                    outlined
                    dense
                    :label="adDepartmentBlank ? t('access.qkaDepartmentRequired') : t('access.departmentOverride')"
                    :hint="t('access.departmentOverrideHint')"
                  />
                  <q-input
                    v-model="userForm.email"
                    outlined
                    dense
                    type="email"
                    :label="t('access.optionalContactEmail')"
                    :hint="t('access.optionalContactEmailHint')"
                  />
                  <q-banner v-if="linkExistingPrompt" dense class="bg-info text-dark">
                    {{ t("access.linkExistingPrompt") }}
                    <template #action>
                      <q-btn flat dense color="primary" :label="t('access.linkExistingConfirm')" @click="confirmLinkExisting" />
                    </template>
                  </q-banner>
                </template>

                <template v-else>
                  <q-input v-model="userForm.displayName" outlined dense :label="t('access.fullName')" />
                  <q-input
                    v-model="userForm.email"
                    outlined
                    dense
                    type="email"
                    :label="t('access.email')"
                    :disable="!!editingUser"
                  />
                </template>

                <q-checkbox v-model="userForm.localChatEnabled" dense :label="t('access.localChat')" />
                <q-checkbox v-model="userForm.mcpEnabled" dense :label="t('access.mcpChat')" />
                <div class="text-caption text-grey-7">{{ t("access.channelDefaultOff") }}</div>
                <div v-if="!editingUser && userForm.identitySource === 'active_directory'" class="text-caption text-warning">
                  {{ t("access.adOnlyLoginBlocker") }}
                </div>
              </q-card-section>
              <q-card-actions align="right">
                <q-btn flat :label="t('access.cancel')" v-close-popup />
                <q-btn
                  color="primary"
                  unelevated
                  :loading="savingUser"
                  :label="editingUser ? t('access.save') : t('access.createUser')"
                  :disable="!canSubmitUser"
                  @click="saveUserDialog"
                />
              </q-card-actions>
            </q-card>
          </q-dialog>

          <q-dialog v-model="importDialog">
            <q-card style="min-width: 420px; max-width: 92vw">
              <q-card-section>
                <div class="text-subtitle1">{{ t("access.importMembers") }}</div>
                <p class="text-caption text-grey-7">{{ membershipNote }}</p>
              </q-card-section>
              <q-card-section class="q-gutter-md">
                <q-file v-model="importFile" outlined dense accept=".csv,text/csv" :label="t('access.csvFile')" />
                <q-checkbox v-model="markMissingNonmember" dense :label="t('access.markMissingNonmember')" />
              </q-card-section>
              <q-card-actions align="right">
                <q-btn flat :label="t('access.cancel')" v-close-popup />
                <q-btn color="primary" unelevated :loading="importing" :disable="!importFile" :label="t('access.importNow')" @click="runMembershipImport" />
              </q-card-actions>
            </q-card>
          </q-dialog>
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
                  <div v-if="selectedEmployee" class="q-mt-md q-gutter-xs">
                    <q-badge :color="identityBadgeColor(selectedEmployee.identityStatus)" :label="identityStatusLabel(selectedEmployee.identityStatus)" />
                    <q-badge outline :color="membershipBadgeColor(selectedEmployee.chatgptMembership)" :label="membershipLabel(selectedEmployee.chatgptMembership)" />
                    <div v-if="selectedEmployee.identityStatus === 'pending'" class="text-caption text-warning q-mt-sm">{{ t("access.pendingHint") }}</div>
                    <div v-if="selectedEmployee.identityStatus === 'disabled'" class="text-caption text-negative q-mt-sm">{{ t("access.disabledHint") }}</div>
                  </div>
                </q-card-section>
              </q-card>

              <q-card bordered flat class="qka-card">
                <q-card-section>
                  <div class="text-subtitle2 q-mb-sm">{{ t("access.identitySummary") }}</div>
                  <div v-if="selectedEmployee" class="text-caption qka-kv">
                    <div><span class="text-grey-7">{{ t("access.fullName") }}</span><br />{{ selectedEmployee.displayName }}</div>
                    <div class="q-mt-sm"><span class="text-grey-7">{{ t("access.googleSub") }}</span><br />{{ selectedEmployee.googleSub || t("access.googleSubMissing") }}</div>
                    <div class="q-mt-sm">
                      <span class="text-grey-7">{{ t("access.filterMembership") }}</span><br />
                      <q-badge outline :color="membershipBadgeColor(selectedEmployee.chatgptMembership)" :label="membershipLabel(selectedEmployee.chatgptMembership)" />
                    </div>
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
                      <div v-if="effectiveDenialLabel" class="q-mt-xs text-grey-7">
                        {{ effectiveDenialLabel }}
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
                  <div v-if="selectedShare?.accessModel === 'qka_managed'" class="text-caption text-positive q-mb-md">
                    {{ t("access.qkaManagedStatus") }}
                  </div>
                  <div v-else-if="selectedShare" class="text-caption text-grey-7 q-mb-md">
                    {{ t("access.sourceAclStatus") }}
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
                    <q-input
                      v-model="folderSearch"
                      outlined
                      dense
                      clearable
                      debounce="300"
                      class="q-mb-sm"
                      :label="t('access.folderSearch')"
                      @update:model-value="onFolderSearch"
                    />
                    <div v-if="folderSearchHits" class="qka-tree q-mb-md">
                      <q-checkbox
                        v-for="hit in folderSearchHits"
                        :key="hit.id"
                        v-model="tickedPaths"
                        :val="hit.path"
                        dense
                        :label="`${hit.label} — ${hit.path}`"
                      />
                      <div v-if="!folderSearchHits.length" class="text-caption text-grey-7">{{ t("access.noFolders") }}</div>
                    </div>
                    <q-tree
                      v-else-if="folderNodes.length"
                      :nodes="folderNodes"
                      node-key="id"
                      tick-strategy="strict"
                      v-model:ticked="tickedPaths"
                      @lazy-load="onFolderLazyLoad"
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
                  <div class="column q-gutter-xs q-mb-md">
                    <q-checkbox v-model="capView" dense :label="t('access.capView')" />
                    <div class="text-caption text-grey-7 q-ml-lg">{{ t("access.capViewHint") }}</div>
                    <q-checkbox v-model="capDownload" dense :label="t('access.capDownload')" />
                    <div class="text-caption text-grey-7 q-ml-lg">{{ t("access.capDownloadHint") }}</div>
                  </div>

                  <q-banner dense rounded class="bg-blue-1 text-dark q-mb-md">
                    <div class="text-caption">{{ t("access.configuredRule") }}: {{ ruleSummary }}</div>
                    <div class="text-caption q-mt-xs">
                      {{ t("access.effective") }}:
                      {{
                        effective?.sampleDocument?.allowed
                          ? selectedShare?.accessModel === "qka_managed"
                            ? t("access.effectiveAllowManaged")
                            : t("access.effectiveAllowConfigured")
                          : effectiveDenialLabel || t("access.effectiveDenySource")
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
                  <div v-if="selectedEmployee?.identityStatus === 'pending'" class="text-caption text-warning q-mt-sm">
                    {{ t("access.pendingGrantHint") }}
                  </div>
                  <div v-else-if="selectedEmployee && !selectedEmployee.canGrant" class="text-caption text-warning q-mt-sm">
                    {{ t("access.grantRequiresLinked") }}
                  </div>
                </q-card-section>
              </q-card>
            </div>
          </div>

        </q-tab-panel>

        <q-tab-panel name="audit" class="q-pa-none">
          <q-card bordered flat class="qka-card">
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
  type DirectoryUser,
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
const tab = ref("users");

const usersError = ref("");
const usersLoading = ref(false);
const userRows = ref<DirectoryUser[]>([]);
const userSearch = ref("");
const filterAccount = ref<string | null>(null);
const filterIdentity = ref<string | null>(null);
const filterMembership = ref<string | null>(null);
const userPagination = ref({ page: 1, rowsPerPage: 25, rowsNumber: 0 });
const userDialog = ref(false);
const editingUser = ref<DirectoryUser | null>(null);
const savingUser = ref(false);
const busyUserId = ref<string | null>(null);
const userForm = ref({
  identitySource: "google" as "google" | "active_directory",
  displayName: "",
  email: "",
  domainUsername: "",
  departmentOverride: "",
  objectGuid: "",
  linkExistingQkaUserId: null as string | null,
  localChatEnabled: false,
  mcpEnabled: false,
});
const selectedAdUser = ref<string | null>(null);
const adPickerOptions = ref<
  Array<{
    label: string;
    value: string;
    objectGuid: string;
    displayName: string;
    samAccountName: string;
    department: string;
    departmentBlank: boolean;
    mail: string | null;
    alreadyLinked: boolean;
    linkedQkaUserId: string | null;
  }>
>([]);
const adPickerLoading = ref(false);
const adDepartmentBlank = ref(false);
const linkExistingPrompt = ref(false);
const pendingLinkUserId = ref<string | null>(null);
const importDialog = ref(false);
const importFile = ref<File | null>(null);
const importing = ref(false);
const markMissingNonmember = ref(false);
const membershipNote = ref("");

const sourceError = ref("");
const employees = ref<SourceEmployee[]>([]);
const sources = ref<SourceSource[]>([]);
const folderNodes = ref<SourceFolderNode[]>([]);
const tickedPaths = ref<string[]>([]);
const folderSearch = ref("");
const folderSearchHits = ref<SourceFolderNode[] | null>(null);
const selectedAllowlistId = ref<string | null>(null);
const selectedShareId = ref<string | null>(null);
const selectedScopeId = ref<string | null>(null);
const eligibility = ref<{
  eligible: boolean;
  scopes: SourceScope[];
  reason: string | null;
  authorizedForDelegation?: boolean;
  accessModel?: "source_acl" | "qka_managed";
  configurationStatus?: string;
} | null>(null);
const effective = ref<SourceEffective | null>(null);
const pathMode = ref<"all" | "selected">("all");
const includeSubfolders = ref(true);
const fileTypes = ref<string[]>(["excel", "word", "pdf", "powerpoint", "images", "csv", "text", "other"]);
const capView = ref(true);
const capDownload = ref(false);
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
  { label: "UserCreated", value: "UserCreated" },
  { label: "UserEdited", value: "UserEdited" },
  { label: "UserEnabled", value: "UserEnabled" },
  { label: "UserDisabled", value: "UserDisabled" },
  { label: "ChannelAccessChanged", value: "ChannelAccessChanged" },
  { label: "MembershipImportCompleted", value: "MembershipImportCompleted" },
];

const accountFilterOptions = computed(() => [
  { label: t("access.accountActive"), value: "active" },
  { label: t("access.accountPending"), value: "pending" },
  { label: t("access.accountDisabled"), value: "disabled" },
]);
const identityFilterOptions = computed(() => [
  { label: t("access.statusLinked"), value: "linked" },
  { label: t("access.statusPending"), value: "pending" },
  { label: t("access.statusDisabled"), value: "disabled" },
]);
const membershipFilterOptions = computed(() => [
  { label: t("access.memberVerified"), value: "verified_member" },
  { label: t("access.memberNonmember"), value: "verified_nonmember" },
  { label: t("access.memberUnknown"), value: "unknown" },
]);

const identitySourceOptions = computed(() => [
  { label: t("access.sourceActiveDirectory"), value: "active_directory" },
  { label: t("access.sourceGoogle"), value: "google" },
]);

const userColumns: QTableProps["columns"] = [
  { name: "displayName", label: "Full Name", field: "displayName", align: "left" },
  { name: "domainUsername", label: "Domain Username", field: "domainUsername", align: "left" },
  { name: "primaryEmail", label: "Email", field: "primaryEmail", align: "left" },
  { name: "department", label: "Department", field: "department", align: "left" },
  { name: "identitySource", label: "Identity Source", field: "identitySource", align: "left" },
  { name: "localChatEnabled", label: "Local AI Chat", field: "localChatEnabled", align: "left" },
  { name: "mcpEnabled", label: "MCP Access", field: "mcpEnabled", align: "left" },
  { name: "accountStatus", label: "Account Status", field: "accountStatus", align: "left" },
  { name: "actions", label: "Actions", field: "actions", align: "right" },
];

const canSubmitUser = computed(() => {
  if (editingUser.value) return !!(userForm.value.displayName || "").trim();
  if (userForm.value.identitySource === "active_directory") {
    if (!(userForm.value.objectGuid || "").trim()) return false;
    if (adDepartmentBlank.value && !(userForm.value.departmentOverride || "").trim()) return false;
    return true;
  }
  return !!(userForm.value.displayName || "").trim() && !!(userForm.value.email || "").trim();
});

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
    label: `${e.displayName} · ${e.primaryEmail} · ${identityStatusLabel(e.identityStatus)} · ${membershipLabel(e.chatgptMembership)}`,
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
const effectiveDenialLabel = computed(() => {
  const code = effective.value?.sampleDocument?.denialReason;
  if (!code) return "";
  if (selectedShare.value?.accessModel === "qka_managed" && code === "no_linked_ad_identity") {
    return t("access.denial.qka_grant_missing");
  }
  const key = `access.denial.${code}`;
  const label = t(key);
  return label === key ? code : label;
});

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
    capView.value ? t("access.capView") : null,
    capDownload.value ? t("access.capDownload") : null,
  ].filter(Boolean);
  if (caps.length) parts.push(caps.join(", "));
  return parts.join(" · ");
});

const canSaveRule = computed(() => {
  if (!selectedEmployee.value?.canGrant || !selectedShareId.value) return false;
  if (!(capView.value || capDownload.value)) return false;
  if (pathMode.value === "selected" && !tickedPaths.value.length) return false;
  return true;
});

function identityStatusLabel(status: SourceEmployee["identityStatus"]) {
  if (status === "linked") return t("access.statusLinked");
  if (status === "pending") return t("access.statusPending");
  if (status === "ad_enrolled") return t("access.statusAdEnrolled");
  return t("access.statusDisabled");
}
function identitySourceLabel(source?: DirectoryUser["identitySource"]) {
  if (source === "ad") return t("access.sourceAdShort");
  if (source === "both") return t("access.sourceBothShort");
  if (source === "google") return t("access.sourceGoogleShort");
  return t("access.sourceNoneShort");
}
function identityBadgeColor(status: SourceEmployee["identityStatus"]) {
  if (status === "linked") return "positive";
  if (status === "pending") return "warning";
  return "negative";
}
function membershipLabel(status: DirectoryUser["chatgptMembership"]) {
  if (status === "verified_member") return t("access.memberVerified");
  if (status === "verified_nonmember") return t("access.memberNonmember");
  return t("access.memberUnknown");
}
function membershipBadgeColor(status: DirectoryUser["chatgptMembership"]) {
  if (status === "verified_member") return "positive";
  if (status === "verified_nonmember") return "grey";
  return "orange";
}
function accountStatusLabel(status: DirectoryUser["accountStatus"]) {
  if (status === "active") return t("access.accountActive");
  if (status === "pending") return t("access.accountPending");
  return t("access.accountDisabled");
}
function accountBadgeColor(status: DirectoryUser["accountStatus"]) {
  if (status === "active") return "positive";
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

async function loadUsers() {
  if (!session.user?.superAdmin) {
    userRows.value = [];
    return;
  }
  usersLoading.value = true;
  try {
    const page = await api.listDirectoryUsers({
      search: userSearch.value || undefined,
      accountStatus: filterAccount.value || undefined,
      identityStatus: filterIdentity.value || undefined,
      chatgptMembership: filterMembership.value || undefined,
      page: userPagination.value.page,
      pageSize: userPagination.value.rowsPerPage,
    });
    userRows.value = page.items;
    userPagination.value = {
      ...userPagination.value,
      rowsNumber: page.total,
      page: page.page,
      rowsPerPage: page.pageSize,
    };
    membershipNote.value = page.membershipSync?.note || t("access.membershipManualNote");
    usersError.value = "";
  } catch (err) {
    usersError.value = err instanceof Error ? err.message : t("access.loadError");
  } finally {
    usersLoading.value = false;
  }
}

function reloadUsers() {
  userPagination.value.page = 1;
  void loadUsers();
}

async function onUsersRequest(req: { pagination: { page: number; rowsPerPage: number } }) {
  userPagination.value.page = req.pagination.page;
  userPagination.value.rowsPerPage = req.pagination.rowsPerPage;
  await loadUsers();
}

function resetUserForm() {
  userForm.value = {
    identitySource: "google",
    displayName: "",
    email: "",
    domainUsername: "",
    departmentOverride: "",
    objectGuid: "",
    linkExistingQkaUserId: null,
    localChatEnabled: false,
    mcpEnabled: false,
  };
  selectedAdUser.value = null;
  adDepartmentBlank.value = false;
  linkExistingPrompt.value = false;
  pendingLinkUserId.value = null;
  adPickerOptions.value = [];
}

function openAddUser() {
  editingUser.value = null;
  resetUserForm();
  userDialog.value = true;
}

function openEditUser(row: DirectoryUser) {
  editingUser.value = row;
  userForm.value = {
    identitySource: row.identitySource === "ad" || row.identitySource === "both" ? "active_directory" : "google",
    displayName: row.displayName,
    email: row.primaryEmail,
    domainUsername: row.domainUsername || "",
    departmentOverride: row.department || "",
    objectGuid: "",
    linkExistingQkaUserId: null,
    localChatEnabled: row.localChatEnabled,
    mcpEnabled: row.mcpEnabled,
  };
  userDialog.value = true;
}

function onIdentitySourceChange() {
  selectedAdUser.value = null;
  userForm.value.objectGuid = "";
  userForm.value.displayName = "";
  userForm.value.domainUsername = "";
  userForm.value.departmentOverride = "";
  userForm.value.email = "";
  userForm.value.linkExistingQkaUserId = null;
  adDepartmentBlank.value = false;
  linkExistingPrompt.value = false;
  pendingLinkUserId.value = null;
}

async function filterAdUsers(val: string, update: (fn: () => void) => void) {
  adPickerLoading.value = true;
  try {
    const page = await api.adEligibleUsers({ search: val || "", pageSize: 25 });
    update(() => {
      adPickerOptions.value = page.items.map((item) => ({
        label: `${item.displayName} · ${item.samAccountName}`,
        value: item.objectGuid,
        objectGuid: item.objectGuid,
        displayName: item.displayName,
        samAccountName: item.samAccountName,
        department: item.department,
        departmentBlank: item.departmentBlank,
        mail: item.mail,
        alreadyLinked: item.alreadyLinked,
        linkedQkaUserId: item.linkedQkaUserId,
      }));
    });
  } catch (err) {
    usersError.value = err instanceof Error ? err.message : t("access.adPickerError");
    update(() => {
      adPickerOptions.value = [];
    });
  } finally {
    adPickerLoading.value = false;
  }
}

function onAdUserSelected(guid: string | null) {
  const hit = adPickerOptions.value.find((o) => o.objectGuid === guid);
  if (!hit) {
    userForm.value.objectGuid = "";
    return;
  }
  if (hit.alreadyLinked && hit.linkedQkaUserId) {
    usersError.value = t("access.adAlreadyLinked", { id: hit.linkedQkaUserId });
  }
  userForm.value.objectGuid = hit.objectGuid;
  userForm.value.displayName = hit.displayName;
  userForm.value.domainUsername = hit.samAccountName;
  userForm.value.departmentOverride = hit.department || "";
  userForm.value.email = hit.mail || "";
  adDepartmentBlank.value = hit.departmentBlank;
  linkExistingPrompt.value = false;
  pendingLinkUserId.value = null;
  userForm.value.linkExistingQkaUserId = null;
}

function confirmLinkExisting() {
  if (!pendingLinkUserId.value) return;
  userForm.value.linkExistingQkaUserId = pendingLinkUserId.value;
  linkExistingPrompt.value = false;
  void saveUserDialog();
}

async function saveUserDialog() {
  savingUser.value = true;
  usersError.value = "";
  try {
    if (editingUser.value) {
      if (editingUser.value.allowlistId) {
        await api.updateDirectoryUser(editingUser.value.allowlistId, {
          displayName: userForm.value.displayName,
          localChatEnabled: userForm.value.localChatEnabled,
          mcpEnabled: userForm.value.mcpEnabled,
        });
      } else if (editingUser.value.qkaUserId) {
        await api.updateAdDirectoryUser(editingUser.value.qkaUserId, {
          displayName: userForm.value.displayName,
          localChatEnabled: userForm.value.localChatEnabled,
          mcpEnabled: userForm.value.mcpEnabled,
        });
      }
    } else if (userForm.value.identitySource === "active_directory") {
      await api.createDirectoryUser({
        identitySource: "active_directory",
        objectGuid: userForm.value.objectGuid,
        displayName: userForm.value.displayName,
        departmentOverride: userForm.value.departmentOverride || null,
        contactEmail: userForm.value.email || null,
        linkExistingQkaUserId: userForm.value.linkExistingQkaUserId,
        localChatEnabled: false,
        mcpEnabled: false,
      });
    } else {
      await api.createDirectoryUser({
        identitySource: "google",
        displayName: userForm.value.displayName,
        email: userForm.value.email,
        localChatEnabled: userForm.value.localChatEnabled,
        mcpEnabled: userForm.value.mcpEnabled,
      });
    }
    userDialog.value = false;
    await loadUsers();
  } catch (err) {
    const message = err instanceof Error ? err.message : t("access.addError");
    const match = /qkaUserId=([0-9a-fA-F-]{36})/.exec(message);
    if (match && userForm.value.identitySource === "active_directory" && !userForm.value.linkExistingQkaUserId) {
      pendingLinkUserId.value = match[1];
      linkExistingPrompt.value = true;
      usersError.value = message;
    } else {
      usersError.value = message;
    }
  } finally {
    savingUser.value = false;
  }
}

async function toggleUserEnabled(row: DirectoryUser) {
  busyUserId.value = row.allowlistId || row.qkaUserId;
  try {
    const enable = row.accountStatus === "disabled";
    if (row.allowlistId) {
      await api.updateDirectoryUser(row.allowlistId, { enabled: enable });
    } else if (row.qkaUserId) {
      await api.updateAdDirectoryUser(row.qkaUserId, { enabled: enable });
    }
    await loadUsers();
  } catch (err) {
    usersError.value = err instanceof Error ? err.message : t("access.updateError");
  } finally {
    busyUserId.value = null;
  }
}

async function runMembershipImport() {
  if (!importFile.value) return;
  importing.value = true;
  usersError.value = "";
  try {
    await api.importMembershipCsv(importFile.value, markMissingNonmember.value);
    importDialog.value = false;
    importFile.value = null;
    await loadUsers();
  } catch (err) {
    usersError.value = err instanceof Error ? err.message : t("access.importError");
  } finally {
    importing.value = false;
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
    const eligible = await api.sourceEligibleScopes(selectedShareId.value);
    eligibility.value = {
      eligible: eligible.eligible,
      scopes: eligible.scopes,
      reason: eligible.reason,
      accessModel: eligible.accessModel as "source_acl" | "qka_managed" | undefined,
    };
    const wholeShare = eligibility.value.scopes.find((s) => !(s.folderPrefixNormalized || "").trim());
    selectedScopeId.value = wholeShare?.id || null;
    folderSearch.value = "";
    folderSearchHits.value = null;
    folderNodes.value = await api.sourceFolderTree(selectedShareId.value, "");
  } catch (err) {
    sourceError.value = err instanceof Error ? err.message : t("access.sourceLoadError");
  }
}

async function onFolderLazyLoad(tick: { node: SourceFolderNode; done: (children: SourceFolderNode[]) => void; fail: () => void }) {
  if (!selectedShareId.value) {
    tick.fail();
    return;
  }
  try {
    const page = await api.sourceFolderChildren(selectedShareId.value, tick.node.path || "", { pageSize: 500 });
    tick.done(page.items || []);
  } catch {
    tick.fail();
  }
}

async function onFolderSearch() {
  const q = folderSearch.value.trim();
  if (!q || !selectedShareId.value) {
    folderSearchHits.value = null;
    return;
  }
  try {
    const found = await api.sourceFolderSearch(selectedShareId.value, q);
    folderSearchHits.value = found.items || [];
  } catch {
    folderSearchHits.value = [];
  }
}

async function onUserSelected() {
  effective.value = null;
  const employee = selectedEmployee.value;
  if (!employee) return;
  if (!employee.qkaUserId || employee.identityStatus !== "linked") return;
  try {
    effective.value = await api.sourceEffective(employee.qkaUserId);
    const grant = effective.value.grants.find((g) => g.status === "active");
    if (grant) {
      pathMode.value = grant.pathMode === "selected" ? "selected" : "all";
      includeSubfolders.value = grant.includeSubfolders;
      tickedPaths.value = [...(grant.selectedPaths || [])];
      fileTypes.value = grant.fileTypes?.length ? [...grant.fileTypes] : [...allFileTypeValues.value];
      capView.value = grant.capabilityView ?? grant.capabilityRead ?? true;
      capDownload.value = !!grant.capabilityDownload;
      const grantPrefix = (grant.scope?.folderPrefixNormalized || "").trim();
      if (grant.scopeId && !grantPrefix) selectedScopeId.value = grant.scopeId;
    }
  } catch (err) {
    sourceError.value = err instanceof Error ? err.message : t("access.sourceLoadError");
  }
}

async function saveRule() {
  const employee = selectedEmployee.value;
  if (!employee?.canGrant || !selectedShareId.value) return;
  granting.value = true;
  sourceError.value = "";
  try {
    const saved = await api.upsertSourceGrant({
      scopeId: selectedScopeId.value || undefined,
      approvedShareId: selectedShareId.value,
      qkaUserId: employee.qkaUserId || undefined,
      allowlistId: employee.allowlistId,
      capabilityView: capView.value,
      capabilityDownload: capDownload.value,
      pathMode: pathMode.value,
      includeSubfolders: includeSubfolders.value,
      selectedPaths: pathMode.value === "selected" ? tickedPaths.value : [],
      fileTypes: allFileTypesSelected.value ? [] : fileTypes.value,
      reason: "access-rule",
    });
    if (saved.scopeId) selectedScopeId.value = saved.scopeId;
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
  if (value === "audit") void loadAudit();
  if (value === "users") void loadUsers();
});

onMounted(async () => {
  await loadUsers();
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
