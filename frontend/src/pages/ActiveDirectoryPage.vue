<template>
  <q-page padding class="qka-ad">
    <div class="row items-center q-mb-md q-gutter-sm">
      <q-btn flat round dense icon="arrow_back" to="/integrations" />
      <div class="col">
        <div class="text-caption text-grey-7">{{ t("nav.integrations") }}</div>
        <h1 class="text-h5 q-my-none">{{ t("ad.title") }}</h1>
        <div class="text-body2 text-grey-7">{{ t("ad.subtitle") }}</div>
      </div>
      <q-badge v-if="connection" :color="badgeColor" outline class="q-pa-sm">
        {{ statusLabel(connection.connectionStatus) }}
      </q-badge>
      <q-badge v-else color="grey" outline class="q-pa-sm">{{ t("ad.notConnected") }}</q-badge>
    </div>

    <q-banner v-if="!session.user?.superAdmin" class="bg-warning text-dark q-mb-md">
      {{ t("ad.adminOnly") }}
    </q-banner>
    <q-banner v-if="error" class="bg-negative text-white q-mb-md">{{ error }}</q-banner>
    <q-banner v-if="info" class="bg-positive text-white q-mb-md">{{ info }}</q-banner>

    <template v-if="session.user?.superAdmin">
      <q-card bordered class="q-mb-md">
        <q-card-section>
          <div class="text-subtitle1 q-mb-sm">{{ t("ad.connectionSettings") }}</div>
          <div class="row q-col-gutter-md">
            <div class="col-12 col-md-6">
              <q-input v-model="form.connectionName" outlined dense :label="t('ad.connectionName')" :disable="!editing" />
            </div>
            <div class="col-12 col-md-6">
              <q-input v-model="form.domainDnsName" outlined dense :label="t('ad.domainDns')" :disable="!editing" hint="e.g. ad.example.local" />
            </div>
            <div class="col-12 col-md-6">
              <q-input v-model="form.domainController" outlined dense :label="t('ad.domainController')" :disable="!editing" hint="e.g. dc01.ad.example.local" />
            </div>
            <div class="col-12 col-md-3">
              <q-select v-model="form.protocol" outlined dense emit-value map-options :options="protocolOptions" :label="t('ad.protocol')" :disable="!editing" />
            </div>
            <div class="col-12 col-md-3">
              <q-input v-model.number="form.port" type="number" outlined dense :label="t('ad.port')" :disable="!editing" />
            </div>
            <div class="col-12">
              <q-input v-model="form.baseDn" outlined dense :label="t('ad.baseDn')" :disable="!editing" :hint="t('ad.baseDnHint')" />
            </div>
          </div>
        </q-card-section>
        <q-card-section>
          <div class="text-subtitle1 q-mb-sm">{{ t("ad.credentials") }}</div>
          <div class="row q-col-gutter-md">
            <div class="col-12 col-md-6">
              <q-input v-model="form.bindUsername" outlined dense :label="t('ad.bindUsername')" :disable="!editing" hint="DOMAIN\qka-reader or UPN" />
            </div>
            <div class="col-12 col-md-6">
              <q-input
                v-model="form.bindPassword"
                outlined
                dense
                type="password"
                autocomplete="new-password"
                :label="t('ad.bindPassword')"
                :disable="!editing"
                :hint="connection?.bindSecretConfigured ? t('ad.passwordKeep') : t('ad.passwordRequired')"
              />
            </div>
            <div class="col-12 col-md-4">
              <q-input v-model.number="form.timeoutSeconds" type="number" outlined dense :label="t('ad.timeout')" :disable="!editing" />
            </div>
            <div class="col-12">
              <div class="row items-center q-gutter-xs text-positive">
                <q-icon name="verified_user" size="18px" />
                <span class="text-body2">{{ t("ad.securityNotice") }}</span>
              </div>
            </div>
          </div>
        </q-card-section>
        <q-card-section>
          <div class="text-subtitle1 q-mb-sm">{{ t("ad.diagnostics") }}</div>
          <q-list bordered separator>
            <q-item v-for="check in checks" :key="check.name">
              <q-item-section>{{ checkLabel(check.name) }}</q-item-section>
              <q-item-section side>
                <q-badge :color="checkColor(check.status)">{{ checkStatusLabel(check.status) }}</q-badge>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-if="testDetail" class="text-caption text-grey-7 q-mt-sm">{{ testDetail }}</div>
        </q-card-section>
        <q-card-actions align="right" class="q-gutter-sm">
          <q-btn v-if="!editing" outline color="primary" :label="t('ad.editConnection')" @click="editing = true" />
          <q-btn outline color="primary" :loading="testing" :label="t('ad.testConnection')" @click="testConnection" />
          <q-btn color="primary" unelevated :disable="!canSave" :loading="saving" :label="t('ad.saveConnection')" @click="saveConnection" />
          <q-btn v-if="connection && connection.connectionStatus !== 3" flat color="warning" :label="t('ad.disable')" @click="disableConnection" />
          <q-btn v-if="connection && connection.connectionStatus === 3" flat color="primary" :label="t('ad.enable')" @click="enableConnection" />
        </q-card-actions>
      </q-card>

      <q-tabs v-model="tab" dense class="text-primary" active-color="primary" indicator-color="primary" align="left">
        <q-tab name="overview" :label="t('ad.tabOverview')" />
        <q-tab name="users" :label="t('ad.tabUsers')" />
        <q-tab name="groups" :label="t('ad.tabGroups')" />
        <q-tab name="history" :label="t('ad.tabHistory')" />
      </q-tabs>
      <q-separator />
      <q-tab-panels v-model="tab" animated>
        <q-tab-panel name="overview">
          <div class="row q-col-gutter-md">
            <div class="col-6 col-md-3" v-for="stat in overviewStats" :key="stat.label">
              <q-card bordered>
                <q-card-section>
                  <div class="text-caption text-grey-7">{{ stat.label }}</div>
                  <div class="text-h6">{{ stat.value }}</div>
                </q-card-section>
              </q-card>
            </div>
          </div>
          <q-banner v-for="(w, i) in overview?.warnings || []" :key="i" class="bg-warning text-dark q-mt-md">{{ w }}</q-banner>
          <div class="row q-gutter-sm q-mt-md">
            <q-btn color="primary" unelevated :disable="!connectionId || syncBusy" :loading="syncing" :label="t('ad.syncNow')" @click="syncNow" />
            <q-btn outline color="primary" :disable="!connectionId || syncBusy" :loading="syncing" :label="t('ad.resync')" @click="resync" />
          </div>
        </q-tab-panel>

        <q-tab-panel name="users">
          <div class="row q-col-gutter-sm q-mb-md items-center">
            <div class="col">
              <q-input v-model="userSearch" outlined dense clearable :label="t('ad.searchUsers')" @keyup.enter="loadUsers" />
            </div>
            <div class="col-auto">
              <q-btn color="primary" unelevated :label="t('ad.search')" @click="loadUsers" />
            </div>
          </div>
          <q-table
            flat
            bordered
            row-key="id"
            :rows="users"
            :columns="userColumns"
            :loading="usersLoading"
            hide-pagination
            :rows-per-page-options="[0]"
          >
            <template #body-cell-mappingLabel="props">
              <q-td :props="props">
                <span :class="mapClass(props.row.mappingStatus)">{{ props.row.mappingLabel }}</span>
              </q-td>
            </template>
            <template #body-cell-actions="props">
              <q-td :props="props">
                <q-btn
                  v-if="props.row.mappingStatus === 1 || props.row.mappingStatus === 0"
                  flat
                  dense
                  color="primary"
                  :label="t('ad.approveLink')"
                  @click="approveLink(props.row)"
                />
                <q-btn
                  v-if="props.row.mappingStatus === 2"
                  flat
                  dense
                  color="negative"
                  :label="t('ad.unlink')"
                  @click="unlink(props.row)"
                />
              </q-td>
            </template>
          </q-table>
        </q-tab-panel>

        <q-tab-panel name="groups">
          <div class="text-subtitle2 q-mb-sm">{{ t("ad.ouBrowser") }}</div>
          <div class="row q-col-gutter-sm q-mb-md">
            <div class="col">
              <q-input v-model="ouSearch" outlined dense clearable :label="t('ad.searchOus')" />
            </div>
            <div class="col-auto">
              <q-btn outline color="primary" :label="t('ad.loadOus')" @click="loadOus" />
            </div>
            <div class="col-auto">
              <q-btn color="primary" unelevated :label="t('ad.saveOuScope')" @click="saveOuScope" />
            </div>
          </div>
          <q-list bordered separator class="q-mb-lg">
            <q-item v-for="ou in ous" :key="ou.distinguishedName">
              <q-item-section avatar>
                <q-checkbox v-model="ou.selected" />
              </q-item-section>
              <q-item-section>
                <q-item-label>{{ ou.name }}</q-item-label>
                <q-item-label caption>{{ ou.distinguishedName }}</q-item-label>
              </q-item-section>
            </q-item>
            <q-item v-if="!ous.length">
              <q-item-section class="text-grey-7">{{ t("ad.noOus") }}</q-item-section>
            </q-item>
          </q-list>
          <div class="text-subtitle2 q-mb-sm">{{ t("ad.securityGroups") }}</div>
          <q-table flat bordered row-key="id" :rows="groups" :columns="groupColumns" hide-pagination :rows-per-page-options="[0]" />
        </q-tab-panel>

        <q-tab-panel name="history">
          <div class="text-subtitle2 q-mb-sm">{{ t("ad.syncHistory") }}</div>
          <q-table flat bordered row-key="id" :rows="history" :columns="historyColumns" hide-pagination :rows-per-page-options="[0]" class="q-mb-lg" />
          <div class="text-subtitle2 q-mb-sm">{{ t("ad.auditLog") }}</div>
          <q-table flat bordered row-key="id" :rows="audit" :columns="auditColumns" hide-pagination :rows-per-page-options="[0]" />
        </q-tab-panel>
      </q-tab-panels>
    </template>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { QTableColumn } from "quasar";
import { api, type AdConnection, type AdOverview } from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const error = ref("");
const info = ref("");
const editing = ref(true);
const testing = ref(false);
const saving = ref(false);
const syncing = ref(false);
const tab = ref("overview");
const connection = ref<AdConnection | null>(null);
const overview = ref<AdOverview | null>(null);
const checks = ref<Array<{ name: string; status: string; detail?: string }>>([
  { name: "dns", status: "not_tested" },
  { name: "tcp", status: "not_tested" },
  { name: "tls", status: "not_tested" },
  { name: "bind", status: "not_tested" },
  { name: "rootdse", status: "not_tested" },
  { name: "search", status: "not_tested" },
]);
const testDetail = ref("");
const lastTestOk = ref(false);
const users = ref<any[]>([]);
const usersLoading = ref(false);
const userSearch = ref("");
const groups = ref<any[]>([]);
const ous = ref<Array<{ name: string; distinguishedName: string; selected: boolean }>>([]);
const ouSearch = ref("");
const history = ref<any[]>([]);
const audit = ref<any[]>([]);

const form = reactive({
  connectionName: "Active Directory",
  domainDnsName: "",
  domainController: "",
  protocol: "ldaps",
  port: 636,
  baseDn: "",
  bindUsername: "",
  bindPassword: "",
  timeoutSeconds: 15,
  verifyTls: true,
});

const protocolOptions = [{ label: "LDAPS (636)", value: "ldaps" }];
const connectionId = computed(() => connection.value?.id || null);
const canSave = computed(() => editing.value && lastTestOk.value);
const syncBusy = computed(() => false);
const badgeColor = computed(() => {
  const s = connection.value?.connectionStatus;
  if (s === 1) return "positive";
  if (s === 2) return "negative";
  if (s === 3) return "warning";
  return "grey";
});

const overviewStats = computed(() => [
  { label: t("ad.usersDiscovered"), value: overview.value?.usersDiscovered ?? "—" },
  { label: t("ad.groupsDiscovered"), value: overview.value?.groupsDiscovered ?? "—" },
  { label: t("ad.selectedOus"), value: overview.value?.selectedOus ?? "—" },
  { label: t("ad.linkedIdentities"), value: overview.value?.linkedIdentities ?? "—" },
  { label: t("ad.pendingMappings"), value: overview.value?.pendingMappings ?? "—" },
  { label: t("ad.conflicts"), value: overview.value?.conflictMappings ?? "—" },
]);

const userColumns = computed<QTableColumn[]>(() => [
  { name: "googleAccount", label: t("ad.colGoogle"), field: "googleAccount", align: "left" },
  { name: "samAccountName", label: t("ad.colAdUser"), field: "samAccountName", align: "left" },
  { name: "department", label: t("ad.colDepartment"), field: "department", align: "left" },
  { name: "mappingLabel", label: t("ad.colIdentityStatus"), field: "mappingLabel", align: "left" },
  {
    name: "accountEnabled",
    label: t("ad.colAccountStatus"),
    field: (row: any) => (row.accountEnabled ? t("ad.enabled") : t("ad.disabled")),
    align: "left",
  },
  { name: "actions", label: "", field: "id", align: "right" },
]);

const groupColumns = computed<QTableColumn[]>(() => [
  { name: "name", label: t("ad.colGroup"), field: "name", align: "left" },
  { name: "objectSid", label: "SID", field: "objectSid", align: "left" },
  { name: "memberCount", label: t("ad.colMembers"), field: "memberCount", align: "right" },
]);

const historyColumns = computed<QTableColumn[]>(() => [
  { name: "createdAt", label: t("ad.colWhen"), field: "createdAt", align: "left" },
  { name: "status", label: t("ad.colStatus"), field: "status", align: "left" },
  { name: "usersDiscovered", label: t("ad.usersDiscovered"), field: "usersDiscovered", align: "right" },
  { name: "pendingCount", label: t("ad.pendingMappings"), field: "pendingCount", align: "right" },
  { name: "safeError", label: t("ad.colError"), field: "safeError", align: "left" },
]);

const auditColumns = computed<QTableColumn[]>(() => [
  { name: "createdAt", label: t("ad.colWhen"), field: "createdAt", align: "left" },
  { name: "action", label: t("ad.colAction"), field: "action", align: "left" },
  { name: "detail", label: t("ad.colDetail"), field: "detail", align: "left" },
]);

function statusLabel(status?: number) {
  if (status === 1) return t("integrations.statusConnected");
  if (status === 2) return t("integrations.statusFailed");
  if (status === 3) return t("integrations.statusDisabled");
  return t("ad.notConnected");
}

function checkLabel(name: string) {
  const map: Record<string, string> = {
    dns: t("ad.checkDns"),
    tcp: t("ad.checkTcp"),
    tls: t("ad.checkTls"),
    bind: t("ad.checkBind"),
    rootdse: t("ad.checkRootDse"),
    search: t("ad.checkSearch"),
  };
  return map[name] || name;
}

function checkColor(status: string) {
  if (status === "pass") return "positive";
  if (status === "failed") return "negative";
  return "grey";
}

function checkStatusLabel(status: string) {
  if (status === "pass") return t("ad.pass");
  if (status === "failed") return t("ad.failed");
  return t("ad.notTested");
}

function mapClass(status: number) {
  if (status === 2) return "text-positive text-weight-medium";
  if (status === 3) return "text-negative";
  return "text-grey-7";
}

function applyConnection(row: AdConnection | null) {
  connection.value = row;
  if (!row) return;
  form.connectionName = row.connectionName;
  form.domainDnsName = row.domainDnsName;
  form.domainController = row.domainController;
  form.protocol = row.protocol || "ldaps";
  form.port = row.port || 636;
  form.baseDn = row.baseDn || "";
  form.bindUsername = row.bindUsername;
  form.bindPassword = "";
  form.timeoutSeconds = row.timeoutSeconds || 15;
  editing.value = row.connectionStatus !== 1;
}

async function refresh() {
  const rows = await api.adConnections();
  applyConnection(rows[0] || null);
  overview.value = await api.adOverview(connectionId.value || undefined);
  if (connectionId.value) {
    await Promise.all([loadUsers(), loadGroups(), loadHistory(), loadAudit(), loadSelectedOus()]);
  }
}

async function testConnection() {
  testing.value = true;
  error.value = "";
  info.value = "";
  try {
    const result = await api.adTestConnection(
      {
        connectionName: form.connectionName,
        domainDnsName: form.domainDnsName,
        domainController: form.domainController,
        protocol: form.protocol,
        port: form.port,
        baseDn: form.baseDn,
        bindUsername: form.bindUsername,
        bindPassword: form.bindPassword || undefined,
        timeoutSeconds: form.timeoutSeconds,
        verifyTls: true,
      },
      connectionId.value || undefined,
    );
    checks.value = result.checks;
    lastTestOk.value = result.succeeded;
    testDetail.value = result.checks.map((c) => c.detail).filter(Boolean).join(" · ");
    if (result.discoveredBaseDn && !form.baseDn) form.baseDn = result.discoveredBaseDn;
    info.value = result.succeeded ? t("ad.testOk") : result.safeError || t("ad.testFail");
    if (!result.succeeded) error.value = result.safeError || t("ad.testFail");
  } catch (err) {
    lastTestOk.value = false;
    error.value = err instanceof Error ? err.message : t("ad.testFail");
  } finally {
    testing.value = false;
  }
}

async function saveConnection() {
  saving.value = true;
  error.value = "";
  try {
    const body = {
      connectionName: form.connectionName,
      domainDnsName: form.domainDnsName,
      domainController: form.domainController,
      protocol: "ldaps",
      port: form.port,
      baseDn: form.baseDn,
      bindUsername: form.bindUsername,
      bindPassword: form.bindPassword || undefined,
      timeoutSeconds: form.timeoutSeconds,
      verifyTls: true,
    };
    const row = connectionId.value
      ? await api.adUpdateConnection(connectionId.value, body)
      : await api.adCreateConnection(body);
    applyConnection(row);
    form.bindPassword = "";
    lastTestOk.value = true;
    editing.value = false;
    info.value = t("ad.saveOk");
    await refresh();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("ad.saveError");
  } finally {
    saving.value = false;
  }
}

async function disableConnection() {
  if (!connectionId.value) return;
  connection.value = await api.adDisableConnection(connectionId.value);
}

async function enableConnection() {
  if (!connectionId.value) return;
  connection.value = await api.adEnableConnection(connectionId.value);
  editing.value = true;
}

async function loadUsers() {
  if (!connectionId.value) return;
  usersLoading.value = true;
  try {
    const body = await api.adUsers(connectionId.value, { search: userSearch.value });
    users.value = body.items;
  } finally {
    usersLoading.value = false;
  }
}

async function loadGroups() {
  if (!connectionId.value) return;
  const body = await api.adGroups(connectionId.value);
  groups.value = body.items;
}

async function loadOus() {
  if (!connectionId.value) return;
  const body = await api.adBrowseOus(connectionId.value, { search: ouSearch.value });
  ous.value = body.items.map((item) => ({ ...item, selected: Boolean(item.selected) }));
}

async function loadSelectedOus() {
  if (!connectionId.value) return;
  const body = await api.adOuScope(connectionId.value);
  if (body.items.length) {
    ous.value = body.items.map((item) => ({
      name: item.name,
      distinguishedName: item.distinguishedName,
      selected: true,
    }));
  }
}

async function saveOuScope() {
  if (!connectionId.value) return;
  await api.adSaveOuScope(
    connectionId.value,
    ous.value.map((item) => ({
      name: item.name,
      distinguishedName: item.distinguishedName,
      selected: item.selected,
    })),
  );
  info.value = t("ad.ouScopeSaved");
  overview.value = await api.adOverview(connectionId.value);
}

async function syncNow() {
  if (!connectionId.value) return;
  syncing.value = true;
  error.value = "";
  try {
    await api.adSync(connectionId.value);
    info.value = t("ad.syncOk");
    await refresh();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("ad.syncError");
  } finally {
    syncing.value = false;
  }
}

async function resync() {
  if (!connectionId.value) return;
  syncing.value = true;
  try {
    await api.adResync(connectionId.value);
    await refresh();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("ad.syncError");
  } finally {
    syncing.value = false;
  }
}

async function approveLink(row: any) {
  if (!connectionId.value) return;
  await api.adApproveLink(connectionId.value, row.id, row.candidateQkaUserId || undefined);
  await loadUsers();
  overview.value = await api.adOverview(connectionId.value);
}

async function unlink(row: any) {
  if (!connectionId.value) return;
  await api.adUnlink(connectionId.value, row.id);
  await loadUsers();
  overview.value = await api.adOverview(connectionId.value);
}

async function loadHistory() {
  if (!connectionId.value) return;
  history.value = await api.adSyncHistory(connectionId.value);
}

async function loadAudit() {
  audit.value = await api.adAudit(connectionId.value || undefined);
}

watch(tab, (value) => {
  if (value === "users") void loadUsers();
  if (value === "groups") {
    void loadGroups();
    void loadOus();
  }
  if (value === "history") {
    void loadHistory();
    void loadAudit();
  }
});

onMounted(async () => {
  if (!session.user?.superAdmin) return;
  try {
    await refresh();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("ad.loadError");
  }
});
</script>
