<template>
  <q-page padding class="qka-workstations">
    <div class="row items-start q-mb-sm">
      <div class="col">
        <div class="text-caption text-grey-7">{{ t("workstations.breadcrumb") }}</div>
        <h1 class="text-h5 q-my-none">{{ t("workstations.title") }}</h1>
        <p class="text-body2 text-grey-7 q-mt-xs q-mb-none">{{ t("workstations.body") }}</p>
      </div>
      <div class="col-auto row q-gutter-sm items-center">
        <q-btn color="primary" outline dense :loading="syncing" :label="t('workstations.sync')" @click="openSyncDialog" />
        <q-btn color="primary" unelevated dense :loading="loading && !rows.length" :label="t('workstations.refresh')" @click="() => reload({ background: false })" />
      </div>
    </div>

    <q-tabs v-model="section" dense class="q-mb-sm" active-color="primary" indicator-color="primary" align="left">
      <q-route-tab name="agents" :label="t('workstations.tabAgents')" :to="{ name: 'employee-computers' }" />
      <q-route-tab name="workstations" :label="t('workstations.tabWorkstations')" :to="{ name: 'workstations' }" />
    </q-tabs>

    <q-banner v-if="error" class="bg-negative text-white q-mb-sm" dense>{{ error }}</q-banner>
    <q-banner v-if="syncNote" class="bg-grey-2 q-mb-sm" dense>{{ syncNote }}</q-banner>

    <q-expansion-item
      v-model="readinessExpanded"
      dense
      class="qka-ws-readiness q-mb-sm"
      :header-class="readinessHeaderClass"
      expand-separator
    >
      <template #header>
        <q-item-section avatar>
          <q-icon :name="deployPrereqs?.deployEnabled ? 'verified' : 'warning'" />
        </q-item-section>
        <q-item-section>
          <q-item-label>{{ t("workstations.deployReadinessTitle") }}</q-item-label>
          <q-item-label caption>
            <span v-if="deployPrereqs?.deployEnabled">
              {{ t("workstations.deployReadyNote", { endpoint: deployPrereqs.httpsEndpoint, group: deployPrereqs.gpoTargetGroup }) }}
            </span>
            <span v-else>
              {{ t("workstations.deployBlockedSummary", { n: (deployPrereqs?.blockers || []).length }) }}
            </span>
            <span v-if="readinessCached" class="q-ml-sm text-grey-6">{{ t("workstations.readinessCached") }}</span>
          </q-item-label>
        </q-item-section>
      </template>
      <div class="q-pa-md">
        <div v-if="(deployPrereqs?.blockers || []).length" class="q-mb-sm">
          <div class="text-subtitle2 q-mb-xs">{{ t("workstations.deployBlockers") }}</div>
          <ul class="q-my-none q-pl-md">
            <li v-for="(b, i) in deployPrereqs?.blockers || []" :key="i">{{ b }}</li>
          </ul>
        </div>
        <div class="text-caption text-grey-7">{{ deployPrereqs?.note || t("workstations.deployNote") }}</div>
        <div class="text-caption text-grey-6 q-mt-xs">{{ t("workstations.deployBrowseNote") }}</div>
      </div>
    </q-expansion-item>

    <div class="row q-col-gutter-sm q-mb-sm">
      <div class="col-6 col-sm-4 col-md-2" v-for="card in summaryCards" :key="card.label">
        <div class="text-caption text-grey-7">{{ card.label }}</div>
        <div class="text-subtitle1 text-weight-medium">{{ loadFailed && !rows.length ? "—" : card.value }}</div>
      </div>
    </div>

    <div class="row qka-ws-layout q-col-gutter-md">
      <!-- OU sidebar -->
      <div class="col-12" :class="ouSidebarCollapsed ? 'col-md-auto' : 'col-md-3'">
        <q-card flat bordered class="qka-ws-sidebar">
          <q-card-section class="row items-center q-py-sm q-px-md">
            <div class="text-subtitle2 col">{{ t("workstations.ouBrowser") }}</div>
            <q-btn
              flat
              dense
              round
              :icon="ouSidebarCollapsed ? 'chevron_right' : 'chevron_left'"
              @click="ouSidebarCollapsed = !ouSidebarCollapsed"
            />
          </q-card-section>
          <q-separator />
          <div v-show="!ouSidebarCollapsed" class="q-pa-sm">
            <div class="text-caption text-grey-7 q-mb-xs">{{ t("workstations.syncScopeLabel") }}</div>
            <div class="q-mb-sm">
              <q-chip
                v-for="s in activeSyncOus"
                :key="s.distinguishedName"
                dense
                outline
                color="primary"
                :label="s.breadcrumb || s.name"
              >
                <q-tooltip>{{ s.distinguishedName }}</q-tooltip>
              </q-chip>
              <div v-if="!activeSyncOus.length" class="text-caption text-grey-6">{{ t("workstations.syncScopeEmpty") }}</div>
            </div>
            <div class="text-caption text-grey-7 q-mb-xs">{{ t("workstations.displayFilterLabel") }}</div>
            <q-btn-group spread class="q-mb-sm full-width">
              <q-btn
                dense
                size="sm"
                :outline="filterMode !== 'all'"
                :color="filterMode === 'all' ? 'primary' : undefined"
                :unelevated="filterMode === 'all'"
                :label="t('workstations.filterAllComputers')"
                @click="setFilterAll"
              />
              <q-btn
                dense
                size="sm"
                :outline="filterMode !== 'selected'"
                :color="filterMode === 'selected' ? 'primary' : undefined"
                :unelevated="filterMode === 'selected'"
                :label="t('workstations.filterSelectedOus')"
                :disable="!tickedOuDns.length"
                @click="setFilterSelected"
              />
            </q-btn-group>
            <q-input
              v-model="ouTreeFilter"
              dense
              outlined
              clearable
              class="q-mb-sm"
              :label="t('workstations.searchOus')"
            />
            <q-toggle
              v-model="includeChildren"
              dense
              color="primary"
              class="q-mb-sm"
              :label="t('workstations.includeChildOus')"
              @update:model-value="onIncludeChildrenChange"
            />
            <div class="text-caption text-grey-6 q-mb-sm">{{ t("workstations.includeChildOusHint") }}</div>
            <div class="row q-gutter-xs q-mb-sm">
              <q-btn flat dense size="sm" color="primary" :label="t('workstations.clearFilters')" @click="clearOuFilters" />
            </div>
            <div v-if="selectedOuBreadcrumbs.length" class="q-mb-sm">
              <q-chip
                v-for="bc in selectedOuBreadcrumbs"
                :key="bc.id"
                dense
                removable
                color="primary"
                text-color="white"
                :label="bc.label"
                @remove="untickOu(bc.id)"
              >
                <q-tooltip>{{ bc.id }}</q-tooltip>
              </q-chip>
            </div>
            <q-tree
              v-if="filteredOuTree.length"
              :nodes="filteredOuTree"
              node-key="id"
              label-key="label"
              children-key="children"
              tick-strategy="strict"
              no-nodes-label=""
              v-model:ticked="tickedOuDns"
              v-model:expanded="expandedOuDns"
              dense
              class="qka-ws-ou-tree"
              @update:ticked="onOuTicked"
            >
              <template #default-header="prop">
                <div class="row items-center no-wrap full-width">
                  <div class="col ellipsis">{{ prop.node.label }}</div>
                  <q-badge color="grey-5" text-color="dark" :label="String(prop.node.count ?? 0)" class="q-ml-xs" />
                </div>
              </template>
            </q-tree>
            <div v-else class="text-caption text-grey-6 q-pa-sm">{{ t("workstations.ouTreeEmpty") }}</div>
            <div class="text-caption text-grey-7 q-mt-sm">
              {{ t("workstations.scopeCount", { n: total }) }}
            </div>
          </div>
        </q-card>
      </div>

      <!-- Main inventory -->
      <div class="col-12" :class="ouSidebarCollapsed ? 'col-md' : 'col-md-9'">
        <div class="row q-col-gutter-sm q-mb-sm items-center">
          <div class="col-12 col-md-3">
            <q-input v-model="search" dense outlined clearable :label="t('workstations.search')" @keyup.enter="() => reload({ page: 1 })" />
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
          <div class="col-6 col-md-2">
            <q-select
              v-model="agentStatus"
              :options="agentOptions"
              dense
              outlined
              clearable
              emit-value
              map-options
              :label="t('workstations.agentStatus')"
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
              v-model="assigned"
              :options="assignedOptions"
              dense
              outlined
              clearable
              emit-value
              map-options
              :label="t('workstations.assignedFilter')"
            />
          </div>
          <div class="col-6 col-md-2">
            <q-select
              v-model="deployStatus"
              :options="deployStatusOptions"
              dense
              outlined
              clearable
              emit-value
              map-options
              :label="t('workstations.deployStatusFilter')"
            />
          </div>
          <div class="col-auto">
            <q-btn color="primary" flat dense :label="t('workstations.applyFilters')" @click="() => reload({ page: 1 })" />
          </div>
          <div class="col-auto">
            <q-btn-dropdown dense flat color="primary" :label="t('workstations.columns')">
              <q-list dense>
                <q-item v-for="col in columnDefs" :key="col.name" tag="label" clickable>
                  <q-item-section side>
                    <q-checkbox v-model="visibleColumns" :val="col.name" dense :disable="col.required" />
                  </q-item-section>
                  <q-item-section>
                    <q-item-label>{{ col.label }}</q-item-label>
                  </q-item-section>
                </q-item>
              </q-list>
            </q-btn-dropdown>
          </div>
        </div>

        <q-banner v-if="selectedRows.length" dense class="bg-primary text-white q-mb-sm qka-ws-actionbar">
          <div class="row items-center q-gutter-sm">
            <div class="col-auto text-body2">{{ t("workstations.selectedCount", { n: selectedRows.length }) }}</div>
            <q-space />
            <q-btn flat dense color="white" :loading="probing" :label="t('workstations.bulkPing')" @click="bulkPing" />
            <q-btn
              flat
              dense
              color="white"
              class="qka-ws-deploy-action"
              :disable="!canDeploySelected"
              :loading="deploying"
              :label="t('workstations.deployAgent')"
              @click="openDeploy"
            >
              <q-tooltip v-if="!canDeploySelected">{{ deployDisabledReason }}</q-tooltip>
            </q-btn>
            <q-btn flat dense color="white" icon="close" @click="selectedRows = []" />
          </div>
        </q-banner>

        <q-linear-progress v-if="probing" indeterminate color="primary" class="q-mb-sm" />
        <div v-if="probeProgress" class="text-caption text-grey-7 q-mb-sm">{{ probeProgress }}</div>
        <q-banner v-if="loadFailed && !loading && !rows.length" class="bg-grey-2 q-mb-sm" dense>
          {{ t("workstations.loadFailedHint") }}
        </q-banner>
        <div class="text-caption text-grey-7 q-mb-xs row items-center">
          <span v-if="!loadFailed || rows.length">{{ t("workstations.tableCount", { shown: rows.length, total }) }}</span>
          <q-spinner v-if="backgroundRefreshing" size="16px" class="q-ml-sm" color="primary" />
          <span v-if="backgroundRefreshing" class="q-ml-xs">{{ t("workstations.backgroundRefresh") }}</span>
        </div>

        <q-table
          flat
          bordered
          dense
          row-key="id"
          class="qka-ws-table"
          :rows="rows"
          :columns="visibleColumnDefs"
          :loading="loading && !rows.length"
          selection="multiple"
          v-model:selected="selectedRows"
          v-model:pagination="pagination"
          :rows-per-page-options="[25, 50, 100, 200]"
          binary-state-sort
          :no-data-label="loadFailed && !rows.length ? t('workstations.noDataBecauseError') : t('workstations.noData')"
          @request="onTableRequest"
          @row-click="onRowClick"
        >
          <template #body-cell-computerName="props">
            <q-td :props="props">
              <a href="#" class="text-primary" @click.prevent="openDetails(props.row)">{{ props.value }}</a>
            </q-td>
          </template>
          <template #body-cell-ou="props">
            <q-td :props="props">
              <span>{{ props.row.ouBreadcrumb || shortOu(props.row.ou) }}</span>
              <q-tooltip>{{ props.row.ou }}</q-tooltip>
            </q-td>
          </template>
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
            <q-td :props="props" @click.stop>
              <q-btn dense flat color="primary" :label="t('workstations.details')" @click="openDetails(props.row)" />
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

        <div class="text-caption text-grey-6 q-mt-sm">{{ t("workstations.icmpNote") }}</div>
        <div class="text-caption text-grey-6">{{ t("workstations.deployInventoryNote") }}</div>
      </div>
    </div>

    <!-- Details drawer -->
    <q-drawer v-model="detailsOpen" side="right" overlay bordered :width="420" class="qka-ws-drawer">
      <div v-if="detailsRow" class="q-pa-md">
        <div class="row items-center q-mb-md">
          <div class="text-h6 col">{{ detailsRow.computerName }}</div>
          <q-btn flat dense round icon="close" @click="detailsOpen = false" />
        </div>
        <q-list dense separator>
          <q-item><q-item-section><q-item-label caption>DNS</q-item-label><q-item-label>{{ detailsRow.dnsHostname || "—" }}</q-item-label></q-item-section></q-item>
          <q-item><q-item-section><q-item-label caption>IP</q-item-label><q-item-label>{{ detailsRow.ipAddress || "—" }}</q-item-label></q-item-section></q-item>
          <q-item>
            <q-item-section>
              <q-item-label caption>{{ t("workstations.colOu") }}</q-item-label>
              <q-item-label>{{ detailsRow.ouBreadcrumb || shortOu(detailsRow.ou) }}</q-item-label>
              <q-item-label caption class="text-wrap">{{ detailsRow.ou }}</q-item-label>
            </q-item-section>
          </q-item>
          <q-item>
            <q-item-section>
              <q-item-label caption>DN</q-item-label>
              <q-item-label class="text-wrap">{{ detailsRow.distinguishedName || "—" }}</q-item-label>
            </q-item-section>
          </q-item>
          <q-item><q-item-section><q-item-label caption>{{ t("workstations.colAgent") }}</q-item-label><q-item-label>{{ detailsRow.agentStatusLabel }}</q-item-label></q-item-section></q-item>
          <q-item><q-item-section><q-item-label caption>{{ t("workstations.colAgentVersion") }}</q-item-label><q-item-label>{{ detailsRow.agentVersion || "—" }}</q-item-label></q-item-section></q-item>
          <q-item><q-item-section><q-item-label caption>{{ t("workstations.colHeartbeat") }}</q-item-label><q-item-label>{{ detailsRow.lastHeartbeatAt || "—" }}</q-item-label></q-item-section></q-item>
          <q-item><q-item-section><q-item-label caption>{{ t("workstations.colDeployStatus") }}</q-item-label><q-item-label>{{ detailsRow.deploymentJob?.status || detailsRow.deployAgent?.status || "—" }}</q-item-label></q-item-section></q-item>
          <q-item v-if="detailsRow.deploymentJob">
            <q-item-section>
              <q-item-label caption>{{ t("workstations.deployHistory") }}</q-item-label>
              <q-item-label caption>attempts: {{ detailsRow.deploymentJob.attemptCount }}</q-item-label>
              <q-item-label caption>created: {{ detailsRow.deploymentJob.createdAt || "—" }}</q-item-label>
              <q-item-label caption>updated: {{ detailsRow.deploymentJob.updatedAt || "—" }}</q-item-label>
              <q-item-label caption>scheduled: {{ detailsRow.deploymentJob.scheduledAt || "—" }}</q-item-label>
              <q-item-label caption>installed: {{ detailsRow.deploymentJob.installedAt || "—" }}</q-item-label>
              <q-item-label v-if="detailsRow.deploymentJob.lastError" class="text-negative">{{ detailsRow.deploymentJob.lastError }}</q-item-label>
            </q-item-section>
          </q-item>
          <q-item>
            <q-item-section>
              <q-item-label caption>Pilot group</q-item-label>
              <q-item-label>
                {{ detailsRow.deployAgent?.pilotGroupMember ? "member" : "not a member" }}
                ({{ detailsRow.deployAgent?.pilotGroup || "—" }})
              </q-item-label>
              <q-item-label caption>{{ detailsRow.deployAgent?.note }}</q-item-label>
            </q-item-section>
          </q-item>
        </q-list>
        <div class="q-mt-md row q-gutter-sm">
          <q-btn outline color="primary" :label="t('workstations.ping')" :loading="probingId === detailsRow.id" @click="pingOne(detailsRow)" />
          <q-btn outline color="primary" :label="t('workstations.assign')" @click="openAssign(detailsRow)" />
        </div>
      </div>
    </q-drawer>

    <!-- Sync dialog (AD sync scope — separate from display filter) -->
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
            :loading="employeesLoading"
            dense
            outlined
            emit-value
            map-options
            clearable
            use-input
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
            color="negative"
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
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  api,
  ApiError,
  type DeployPrerequisites,
  type DirectoryUser,
  type Workstation,
  type WorkstationOuTreeNode,
} from "@/api";

const VIEW_KEY = "qka.workstations.view.v3";
const { t } = useI18n();
const section = ref("workstations");
const loading = ref(false);
const backgroundRefreshing = ref(false);
/** Suppress filter watchers / QTable @request until the first intentional load finishes. */
const viewHydrated = ref(false);
let filterWatchEnabled = false;
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
const readinessCached = ref(false);
const readinessExpanded = ref(false);
const deployOpen = ref(false);
const deployConfirmation = ref("");
const tokenOpen = ref(false);
const tokenInstruction = ref("");
const tokenJobs = ref<Array<{ id: string; computerName: string; machineEnrollmentToken: string }>>([]);
const total = ref(0);
const summary = ref({ totalPresent: 0, assigned: 0, reachable: 0, noReply: 0, dnsFailed: 0, unchecked: 0 });
const ouTree = ref<WorkstationOuTreeNode[]>([]);
const activeSyncOus = ref<Array<{ name: string; distinguishedName: string; breadcrumb?: string }>>([]);
const search = ref("");
const reachability = ref<string | null>(null);
const accountStatus = ref<string | null>(null);
const agentStatus = ref<string | null>(null);
const assigned = ref<string | null>(null);
const deployStatus = ref<string | null>(null);
const includeChildren = ref(true);
const filterMode = ref<"all" | "selected">("all");
const tickedOuDns = ref<string[]>([]);
const expandedOuDns = ref<string[]>([]);
const ouTreeFilter = ref("");
const ouSidebarCollapsed = ref(false);
const employees = ref<DirectoryUser[]>([]);
const employeesLoading = ref(false);
const employeesLoaded = ref(false);
const assignOpen = ref(false);
const assignTarget = ref<Workstation | null>(null);
const assignUserId = ref<string | null>(null);
const detailsOpen = ref(false);
const detailsRow = ref<Workstation | null>(null);
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
let reloadSeq = 0;
let filterDebounce: ReturnType<typeof setTimeout> | null = null;

const PRIMARY_COLUMNS = [
  "computerName",
  "ou",
  "accountStatus",
  "assignedEmployeeName",
  "reachabilityStatus",
  "agentStatusLabel",
  "deployStatus",
  "actions",
];
const visibleColumns = ref<string[]>([...PRIMARY_COLUMNS]);

const selectedSyncOuCount = computed(() => Object.keys(selectedOuMap.value).length);
const canOuGoUp = computed(() => {
  const parent = (syncParentDn.value || "").toLowerCase();
  const base = (syncBaseDn.value || "").toLowerCase();
  return Boolean(parent && base && parent !== base);
});

const columnDefs = computed(() => [
  { name: "computerName", label: t("workstations.colComputer"), field: "computerName", align: "left" as const, sortable: true, required: true },
  { name: "dnsHostname", label: t("workstations.colDns"), field: "dnsHostname", align: "left" as const, sortable: true, required: false },
  { name: "ipAddress", label: t("workstations.colIp"), field: "ipAddress", align: "left" as const, sortable: true, required: false },
  {
    name: "ou",
    label: t("workstations.colOu"),
    field: (r: Workstation) => r.ouBreadcrumb || r.ou,
    align: "left" as const,
    sortable: true,
    required: true,
  },
  { name: "accountStatus", label: t("workstations.colAccount"), field: "accountStatus", align: "left" as const, required: false },
  {
    name: "assignedEmployeeName",
    label: t("workstations.colEmployee"),
    field: (r: Workstation) => r.assignedEmployeeName || "—",
    align: "left" as const,
    required: false,
  },
  { name: "reachabilityStatus", label: t("workstations.colReach"), field: "reachabilityStatus", align: "left" as const, required: false },
  {
    name: "lastCheckedAt",
    label: t("workstations.colLastChecked"),
    field: (r: Workstation) => r.lastCheckedAt || "—",
    align: "left" as const,
    sortable: true,
    required: false,
  },
  { name: "agentStatusLabel", label: t("workstations.colAgent"), field: "agentStatusLabel", align: "left" as const, required: false },
  {
    name: "deployStatus",
    label: t("workstations.colDeployStatus"),
    field: (r: Workstation) => r.deploymentJob?.status || r.deployAgent?.status || "—",
    align: "left" as const,
    required: false,
  },
  { name: "agentVersion", label: t("workstations.colAgentVersion"), field: (r: Workstation) => r.agentVersion || "—", align: "left" as const, required: false },
  {
    name: "lastHeartbeatAt",
    label: t("workstations.colHeartbeat"),
    field: (r: Workstation) => r.lastHeartbeatAt || "—",
    align: "left" as const,
    required: false,
  },
  {
    name: "indexingStatus",
    label: t("workstations.colIndexing"),
    field: (r: Workstation) => r.indexingStatus,
    align: "left" as const,
    required: false,
  },
  { name: "actions", label: t("workstations.colActions"), field: "id", align: "right" as const, required: true },
]);

const visibleColumnDefs = computed(() => columnDefs.value.filter((c) => visibleColumns.value.includes(c.name)));

const filteredOuTree = computed(() => {
  const q = ouTreeFilter.value.trim().toLowerCase();
  if (!q) return ouTree.value;
  const filterNodes = (nodes: WorkstationOuTreeNode[]): WorkstationOuTreeNode[] => {
    const out: WorkstationOuTreeNode[] = [];
    for (const n of nodes) {
      const kids = filterNodes(n.children || []);
      const hit =
        n.label.toLowerCase().includes(q) ||
        (n.breadcrumb || "").toLowerCase().includes(q) ||
        n.id.toLowerCase().includes(q);
      if (hit || kids.length) {
        out.push({ ...n, children: kids.length ? kids : n.children });
      }
    }
    return out;
  };
  return filterNodes(ouTree.value);
});

const selectedOuBreadcrumbs = computed(() => {
  const map = new Map<string, string>();
  const walk = (nodes: WorkstationOuTreeNode[]) => {
    for (const n of nodes) {
      map.set(n.id, n.breadcrumb || n.label);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(ouTree.value);
  return tickedOuDns.value.map((id) => ({ id, label: map.get(id) || shortOu(id) }));
});

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
const agentOptions = [
  { label: t("workstations.agent.not_registered"), value: "not_registered" },
  { label: t("workstations.agent.connected"), value: "connected" },
  { label: t("workstations.agent.registered"), value: "registered" },
  { label: t("workstations.agent.registered_offline"), value: "registered_offline" },
];
const assignedOptions = [
  { label: t("workstations.assigned.yes"), value: "yes" },
  { label: t("workstations.assigned.no"), value: "no" },
];
const deployStatusOptions = [
  { label: t("workstations.deployStatus.none"), value: "none" },
  { label: t("workstations.deployStatus.ready"), value: "ready" },
  { label: t("workstations.deployStatus.scheduled"), value: "scheduled" },
  { label: t("workstations.deployStatus.waiting_for_computer"), value: "waiting_for_computer" },
  { label: t("workstations.deployStatus.failed"), value: "failed" },
  { label: t("workstations.deployStatus.cancelled"), value: "cancelled" },
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
const readinessHeaderClass = computed(() =>
  deployPrereqs.value?.deployEnabled ? "bg-green-1 text-dark" : "bg-orange-1 text-dark",
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
    return blocked
      .map((r) => {
        if (r.deployAgent?.pilotGroupMember === false) {
          return t("workstations.deployNotInPilotGroup", {
            name: r.computerName,
            group: r.deployAgent?.pilotGroup || deployPrereqs.value?.gpoTargetGroup || "QKA-Agent-Pilot-Computers",
          });
        }
        return `${r.computerName}: ${r.deployAgent?.note || ""}`;
      })
      .join(" ");
  }
  return "";
});

function shortOu(dn: string) {
  const parts = (dn || "").split(",").map((p) => p.trim()).filter((p) => p.toUpperCase().startsWith("OU="));
  if (!parts.length) return dn || "—";
  return parts.map((p) => p.slice(3)).reverse().join(" / ");
}

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

function persistView() {
  try {
    sessionStorage.setItem(
      VIEW_KEY,
      JSON.stringify({
        search: search.value,
        reachability: reachability.value,
        accountStatus: accountStatus.value,
        agentStatus: agentStatus.value,
        assigned: assigned.value,
        deployStatus: deployStatus.value,
        includeChildren: includeChildren.value,
        filterMode: filterMode.value,
        tickedOuDns: tickedOuDns.value,
        expandedOuDns: expandedOuDns.value,
        ouSidebarCollapsed: ouSidebarCollapsed.value,
        visibleColumns: visibleColumns.value,
        readinessExpanded: readinessExpanded.value,
        pagination: {
          page: pagination.value.page,
          rowsPerPage: pagination.value.rowsPerPage,
          sortBy: pagination.value.sortBy,
          descending: pagination.value.descending,
        },
      }),
    );
  } catch {
    /* ignore */
  }
}

function restoreView() {
  try {
    const raw = sessionStorage.getItem(VIEW_KEY);
    if (!raw) return;
    const v = JSON.parse(raw) as Record<string, unknown>;
    search.value = String(v.search || "");
    reachability.value = (v.reachability as string) || null;
    accountStatus.value = (v.accountStatus as string) || null;
    agentStatus.value = (v.agentStatus as string) || null;
    assigned.value = (v.assigned as string) || null;
    deployStatus.value = (v.deployStatus as string) || null;
    includeChildren.value = v.includeChildren !== false;
    filterMode.value = v.filterMode === "selected" ? "selected" : "all";
    tickedOuDns.value = Array.isArray(v.tickedOuDns) ? (v.tickedOuDns as string[]) : [];
    expandedOuDns.value = Array.isArray(v.expandedOuDns) ? (v.expandedOuDns as string[]) : [];
    ouSidebarCollapsed.value = Boolean(v.ouSidebarCollapsed);
    readinessExpanded.value = Boolean(v.readinessExpanded);
    if (Array.isArray(v.visibleColumns) && (v.visibleColumns as string[]).length) {
      visibleColumns.value = v.visibleColumns as string[];
    }
    const p = (v.pagination || {}) as Record<string, unknown>;
    if (p.page) pagination.value.page = Number(p.page) || 1;
    if (p.rowsPerPage) pagination.value.rowsPerPage = Number(p.rowsPerPage) || 25;
    if (p.sortBy) pagination.value.sortBy = String(p.sortBy);
    pagination.value.descending = Boolean(p.descending);
  } catch {
    /* ignore */
  }
}

async function reload(opts?: {
  page?: number;
  rowsPerPage?: number;
  sortBy?: string;
  descending?: boolean;
  background?: boolean;
}) {
  const pageNum = opts?.page ?? pagination.value.page;
  const rowsPerPage = opts?.rowsPerPage ?? pagination.value.rowsPerPage;
  const sortBy = opts?.sortBy ?? pagination.value.sortBy ?? "computerName";
  const descending = opts?.descending ?? pagination.value.descending;
  // Keep existing rows visible whenever we already have data (or caller asked for background).
  const background = opts?.background === true || rows.value.length > 0;
  const seq = ++reloadSeq;
  if (background) backgroundRefreshing.value = true;
  else loading.value = true;
  if (!background) error.value = "";
  try {
    const filterDns =
      filterMode.value === "selected" && tickedOuDns.value.length ? [...tickedOuDns.value] : [];
    const page = await api.workstations({
      search: search.value || undefined,
      filterOuDns: filterDns.length ? filterDns : undefined,
      includeChildren: includeChildren.value,
      // Display inventory is never limited to the AD sync OU basket.
      scopeOnly: false,
      reachability: reachability.value || undefined,
      accountStatus: accountStatus.value || undefined,
      agentStatus: agentStatus.value || undefined,
      assigned: assigned.value || undefined,
      deployStatus: deployStatus.value || undefined,
      sort: sortBy || "computerName",
      sortDir: descending ? "desc" : "asc",
      page: pageNum,
      pageSize: rowsPerPage,
    });
    if (seq !== reloadSeq) return;
    loadFailed.value = false;
    rows.value = page.items;
    total.value = page.total;
    summary.value = page.summary;
    ouTree.value = Array.isArray(page.ouTree) ? page.ouTree : [];
    activeSyncOus.value = page.activeSyncOus || [];
    readinessCached.value = Boolean(page.readinessCached);
    if (!expandedOuDns.value.length && ouTree.value.length) {
      expandedOuDns.value = ouTree.value.slice(0, 5).map((n) => n.id);
    }
    pagination.value.rowsNumber = page.total;
    pagination.value.page = page.page;
    pagination.value.rowsPerPage = page.pageSize;
    pagination.value.sortBy = sortBy || "computerName";
    pagination.value.descending = descending;
    const keep = new Set(selectedRows.value.map((r) => r.id));
    selectedRows.value = rows.value.filter((r) => keep.has(r.id));
    if (detailsRow.value) {
      detailsRow.value = rows.value.find((r) => r.id === detailsRow.value?.id) || detailsRow.value;
    }
    persistView();
  } catch (err) {
    if (seq !== reloadSeq) return;
    loadFailed.value = true;
    // Preserve existing rows on refresh failure — only clear when we had nothing.
    if (!rows.value.length) {
      total.value = 0;
      summary.value = { totalPresent: 0, assigned: 0, reachable: 0, noReply: 0, dnsFailed: 0, unchecked: 0 };
      pagination.value.rowsNumber = 0;
    }
    error.value = formatApiFailure(err, t("workstations.loadError"));
  } finally {
    if (seq === reloadSeq) {
      loading.value = false;
      backgroundRefreshing.value = false;
    }
  }
}

function setFilterAll() {
  filterMode.value = "all";
  void reload({ page: 1, background: rows.value.length > 0 });
}
function setFilterSelected() {
  if (!tickedOuDns.value.length) return;
  filterMode.value = "selected";
  void reload({ page: 1, background: rows.value.length > 0 });
}
function clearOuFilters() {
  tickedOuDns.value = [];
  filterMode.value = "all";
  void reload({ page: 1, background: rows.value.length > 0 });
}
function onOuTicked(next: string[]) {
  tickedOuDns.value = next;
  // Ticking alone does not change inventory unless Selected OUs mode is already active.
  if (filterMode.value === "selected") {
    if (!next.length) filterMode.value = "all";
    scheduleReload();
  }
  persistView();
}
function onIncludeChildrenChange() {
  if (filterMode.value === "selected") scheduleReload();
  else persistView();
}
function untickOu(id: string) {
  tickedOuDns.value = tickedOuDns.value.filter((d) => d !== id);
  if (!tickedOuDns.value.length && filterMode.value === "selected") {
    filterMode.value = "all";
  }
  if (filterMode.value === "selected" || !tickedOuDns.value.length) {
    scheduleReload();
  } else {
    persistView();
  }
}
function scheduleReload() {
  if (!filterWatchEnabled) return;
  if (filterDebounce) clearTimeout(filterDebounce);
  filterDebounce = setTimeout(() => void reload({ page: 1, background: rows.value.length > 0 }), 250);
}

function openDetails(row: Workstation) {
  detailsRow.value = row;
  detailsOpen.value = true;
}
function onRowClick(_evt: Event, row: Workstation) {
  openDetails(row);
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
    // After sync, show full inventory (display filter stays independent of sync scope).
    filterMode.value = "all";
    tickedOuDns.value = [];
    pagination.value.page = 1;
    await reload({ page: 1, background: false });
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
    await reload({ background: true });
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
    await reload({ background: true });
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.pingError"));
    probeProgress.value = "";
  } finally {
    probing.value = false;
  }
}

async function ensureEmployees() {
  if (employeesLoaded.value || employeesLoading.value) return;
  employeesLoading.value = true;
  try {
    const userPage = await api.listDirectoryUsers({ pageSize: 200 });
    employees.value = userPage.items;
    employeesLoaded.value = true;
  } catch {
    employees.value = [];
  } finally {
    employeesLoading.value = false;
  }
}

async function openAssign(row: Workstation) {
  assignTarget.value = row;
  assignUserId.value = row.assignedEmployeeId;
  assignOpen.value = true;
  await ensureEmployees();
}

async function saveAssign() {
  if (!assignTarget.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api.assignWorkstationEmployee(assignTarget.value.id, assignUserId.value);
    assignOpen.value = false;
    await reload({ background: true });
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.assignError"));
  } finally {
    busy.value = false;
  }
}

async function loadDeployPrereqs() {
  try {
    deployPrereqs.value = await api.workstationDeployPrerequisites();
    if (deployPrereqs.value && !deployPrereqs.value.deployEnabled && (deployPrereqs.value.blockers || []).length) {
      // keep collapsed by default; user expands
    }
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
    const staged = (result.jobs || []).filter((j) => (j as { tokenStaged?: boolean }).tokenStaged);
    const withTokens = (result.jobs || [])
      .filter((j) => j.machineEnrollmentToken)
      .map((j) => ({
        id: j.id,
        computerName: j.computerName,
        machineEnrollmentToken: String(j.machineEnrollmentToken),
      }));
    if (staged.length && !withTokens.length) {
      syncNote.value = `${result.note} ${result.tokenProvisioning?.instruction || t("workstations.tokenAutoStaged")}`;
    } else if (withTokens.length) {
      tokenJobs.value = withTokens;
      tokenInstruction.value = result.tokenProvisioning?.instruction || t("workstations.tokenHint");
      tokenOpen.value = true;
    }
    await reload({ background: true });
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
    await reload({ background: true });
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
    await reload({ background: true });
    await loadDeployPrereqs();
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.deployError"));
  } finally {
    jobBusyId.value = null;
  }
}

async function onTableRequest(req: {
  pagination: { page: number; rowsPerPage: number; sortBy?: string | null; descending?: boolean };
}) {
  // Ignore Quasar's automatic first @request — onMounted owns the initial fetch.
  if (!viewHydrated.value) return;
  const { page, rowsPerPage, sortBy, descending } = req.pagination;
  await reload({
    page,
    rowsPerPage: rowsPerPage || 25,
    sortBy: sortBy || "computerName",
    descending: !!descending,
    background: rows.value.length > 0,
  });
}

watch([reachability, accountStatus, agentStatus, assigned, deployStatus], () => {
  if (!filterWatchEnabled) return;
  scheduleReload();
});

onBeforeRouteLeave(() => {
  persistView();
});

onBeforeUnmount(() => {
  persistView();
  if (filterDebounce) clearTimeout(filterDebounce);
});

onMounted(async () => {
  restoreView();
  // Drop invalid persisted Selected mode (no ticks → All Computers).
  if (filterMode.value === "selected" && !tickedOuDns.value.length) {
    filterMode.value = "all";
  }
  // Inventory loads independently of the employee assignment picker.
  await Promise.all([reload({ background: false }), loadDeployPrereqs()]);
  viewHydrated.value = true;
  filterWatchEnabled = true;
});
</script>

<style scoped>
.qka-ws-sidebar {
  max-height: calc(100vh - 220px);
  overflow: auto;
}
.qka-ws-ou-tree {
  max-height: 420px;
  overflow: auto;
}
.qka-ws-table :deep(tbody tr) {
  cursor: pointer;
}
.qka-ws-drawer .text-wrap {
  word-break: break-all;
  white-space: normal;
}
.qka-ws-deploy-action {
  border: 1px solid rgba(255, 255, 255, 0.45);
}
.body--dark .qka-ws-readiness :deep(.q-item) {
  color: inherit;
}
</style>
