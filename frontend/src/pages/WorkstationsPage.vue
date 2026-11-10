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
        <div class="q-mb-sm">
          <div class="text-subtitle2">{{ t("workstations.deployMethodLabel") }}</div>
          <div class="text-body2">{{ t("workstations.deployMethodValue") }}</div>
          <div class="text-caption text-grey-7">{{ t("workstations.deployMethodHint") }}</div>
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
        <div class="qka-ctrl-toolbar">
          <q-input
            v-model="search"
            class="qka-ctrl-field qka-ctrl-search"
            dense
            outlined
            clearable
            hide-bottom-space
            :placeholder="t('workstations.search')"
            :aria-label="t('workstations.search')"
            @keyup.enter="() => reload({ page: 1 })"
          >
            <template #prepend>
              <q-icon name="search" size="16px" />
            </template>
          </q-input>
          <q-select
            v-model="accountStatus"
            class="qka-ctrl-field qka-ctrl-select--compact"
            :options="accountOptions"
            dense
            outlined
            clearable
            hide-bottom-space
            emit-value
            map-options
            options-dense
            :display-value="wsFilterDisplay(accountStatus, accountOptions, t('workstations.accountStatus'))"
            :aria-label="t('workstations.accountStatus')"
            popup-content-class="qka-ctrl-menu"
          >
            <template #prepend>
              <q-icon name="badge" size="16px" />
            </template>
            <template #option="scope">
              <q-item v-bind="scope.itemProps" :class="{ 'qka-ctrl-menu__active': scope.selected }">
                <q-item-section avatar>
                  <q-icon :name="scope.opt.value === 'enabled' ? 'check_circle' : 'block'" size="16px" />
                </q-item-section>
                <q-item-section>
                  <q-item-label>{{ scope.opt.label }}</q-item-label>
                </q-item-section>
              </q-item>
            </template>
          </q-select>
          <q-select
            v-model="agentStatus"
            class="qka-ctrl-field qka-ctrl-select--compact"
            :options="agentOptions"
            dense
            outlined
            clearable
            hide-bottom-space
            emit-value
            map-options
            options-dense
            :display-value="wsFilterDisplay(agentStatus, agentOptions, t('workstations.agentStatus'))"
            :aria-label="t('workstations.agentStatus')"
            popup-content-class="qka-ctrl-menu"
          >
            <template #prepend>
              <q-icon name="devices" size="16px" />
            </template>
            <template #option="scope">
              <q-item v-bind="scope.itemProps" :class="{ 'qka-ctrl-menu__active': scope.selected }">
                <q-item-section avatar>
                  <q-icon
                    :name="
                      scope.opt.value === 'connected'
                        ? 'wifi'
                        : scope.opt.value === 'registered_offline'
                          ? 'wifi_off'
                          : scope.opt.value === 'registered'
                            ? 'dns'
                            : 'phonelink_off'
                    "
                    size="16px"
                  />
                </q-item-section>
                <q-item-section>
                  <q-item-label>{{ scope.opt.label }}</q-item-label>
                </q-item-section>
              </q-item>
            </template>
          </q-select>
          <q-select
            v-model="reachability"
            class="qka-ctrl-field qka-ctrl-select--compact"
            :options="reachOptions"
            dense
            outlined
            clearable
            hide-bottom-space
            emit-value
            map-options
            options-dense
            :display-value="wsFilterDisplay(reachability, reachOptions, t('workstations.reachability'))"
            :aria-label="t('workstations.reachability')"
            popup-content-class="qka-ctrl-menu"
          >
            <template #prepend>
              <q-icon name="network_check" size="16px" />
            </template>
            <template #option="scope">
              <q-item v-bind="scope.itemProps" :class="{ 'qka-ctrl-menu__active': scope.selected }">
                <q-item-section avatar>
                  <q-icon name="network_check" size="16px" />
                </q-item-section>
                <q-item-section>
                  <q-item-label>{{ scope.opt.label }}</q-item-label>
                </q-item-section>
              </q-item>
            </template>
          </q-select>
          <q-select
            v-model="assigned"
            class="qka-ctrl-field qka-ctrl-select--compact"
            :options="assignedOptions"
            dense
            outlined
            clearable
            hide-bottom-space
            emit-value
            map-options
            options-dense
            :display-value="wsFilterDisplay(assigned, assignedOptions, t('workstations.assignedFilter'))"
            :aria-label="t('workstations.assignedFilter')"
            popup-content-class="qka-ctrl-menu"
          >
            <template #prepend>
              <q-icon name="group" size="16px" />
            </template>
            <template #option="scope">
              <q-item v-bind="scope.itemProps" :class="{ 'qka-ctrl-menu__active': scope.selected }">
                <q-item-section avatar>
                  <q-icon :name="scope.opt.value === 'yes' ? 'person' : 'person_off'" size="16px" />
                </q-item-section>
                <q-item-section>
                  <q-item-label>{{ scope.opt.label }}</q-item-label>
                </q-item-section>
              </q-item>
            </template>
          </q-select>
          <q-select
            v-model="deployStatus"
            class="qka-ctrl-field qka-ctrl-select--compact"
            :options="deployStatusOptions"
            dense
            outlined
            clearable
            hide-bottom-space
            emit-value
            map-options
            options-dense
            :display-value="wsFilterDisplay(deployStatus, deployStatusOptions, t('workstations.deployStatusFilter'))"
            :aria-label="t('workstations.deployStatusFilter')"
            popup-content-class="qka-ctrl-menu"
          >
            <template #prepend>
              <q-icon name="system_update_alt" size="16px" />
            </template>
            <template #option="scope">
              <q-item v-bind="scope.itemProps" :class="{ 'qka-ctrl-menu__active': scope.selected }">
                <q-item-section avatar>
                  <q-icon name="system_update_alt" size="16px" />
                </q-item-section>
                <q-item-section>
                  <q-item-label>{{ scope.opt.label }}</q-item-label>
                </q-item-section>
              </q-item>
            </template>
          </q-select>
          <div class="qka-ctrl-toolbar-actions">
            <q-btn color="primary" flat dense :label="t('workstations.applyFilters')" @click="() => reload({ page: 1 })" />
            <q-btn-dropdown dense flat color="primary" :label="t('workstations.columns')" menu-self="top right">
              <q-list dense class="qka-ctrl-menu" style="min-width: 220px; box-shadow: none; border: none; padding: 0">
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
              :disable="!canBulkAddPilot"
              :loading="pilotBusy"
              :label="t('workstations.pilotAddSelected')"
              @click="openPilotMembership('add', selectedRows)"
            />
            <q-btn
              flat
              dense
              color="white"
              :disable="!canBulkRemovePilot"
              :loading="pilotBusy"
              :label="t('workstations.pilotRemoveSelected')"
              @click="openPilotMembership('remove', selectedRows)"
            />
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
          class="qka-ws-table qka-ctrl-table"
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
              <a href="#" class="qka-ctrl-table__primary text-primary" @click.prevent="openDetails(props.row)">{{ props.value }}</a>
              <div v-if="props.row.assignedEmployeeName" class="qka-ctrl-table__secondary">
                {{ props.row.assignedEmployeeName }}
              </div>
            </q-td>
          </template>
          <template #body-cell-ou="props">
            <q-td :props="props">
              <span class="qka-ctrl-table__secondary">{{ props.row.ouBreadcrumb || shortOu(props.row.ou) }}</span>
              <q-tooltip>{{ props.row.ou }}</q-tooltip>
            </q-td>
          </template>
          <template #body-cell-accountStatus="props">
            <q-td :props="props">
              <q-chip
                dense
                class="qka-ctrl-chip"
                :class="props.row.accountEnabled ? 'qka-ctrl-chip--online' : 'qka-ctrl-chip--neutral'"
                :label="props.value"
              />
            </q-td>
          </template>
          <template #body-cell-reachabilityStatus="props">
            <q-td :props="props">
              <q-chip
                dense
                class="qka-ctrl-chip"
                :class="wsReachChipClass(props.row.reachabilityStatus)"
                :label="reachLabel(props.row.reachabilityStatus)"
              />
            </q-td>
          </template>
          <template #body-cell-agentStatusLabel="props">
            <q-td :props="props">
              <q-chip
                dense
                class="qka-ctrl-chip qka-ctrl-chip--outline"
                :class="wsAgentChipClass(props.row.agentStatus)"
                :label="agentDisplayLabel(props.row)"
              />
            </q-td>
          </template>
          <template #body-cell-pilotMembership="props">
            <q-td :props="props">
              <q-chip
                dense
                class="qka-ctrl-chip qka-ctrl-chip--outline"
                :class="props.row.deployAgent?.pilotGroupMember ? 'qka-ctrl-chip--indexing' : 'qka-ctrl-chip--neutral'"
                :label="pilotMembershipLabel(props.row)"
              />
            </q-td>
          </template>
          <template #body-cell-deployStatus="props">
            <q-td :props="props">
              <div class="qka-ws-deploy-status">
                <div class="qka-ws-deploy-status__line">
                  <q-chip
                    dense
                    square
                    class="qka-ws-deploy-status__chip"
                    :outline="deployStatusOutline(rowDisplayStatus(props.row))"
                    :color="deployStatusColor(rowDisplayStatus(props.row))"
                    :text-color="deployStatusOutline(rowDisplayStatus(props.row)) ? undefined : 'white'"
                    :label="primaryDeployLabel(props.row)"
                  >
                    <q-spinner
                      v-if="rowDisplayStatus(props.row) === 'in_progress'"
                      size="12px"
                      class="q-mr-xs"
                      color="white"
                    />
                  </q-chip>
                </div>
                <div
                  v-for="timeInfo in [deployStatusTimeInfo(props.row)]"
                  v-show="timeInfo"
                  :key="timeInfo?.iso || 'no-time'"
                  class="qka-ws-deploy-status__time text-caption text-grey-7"
                >
                  <template v-if="timeInfo">
                    {{ timeInfo.label }}
                    <q-tooltip anchor="top middle" self="bottom middle">
                      {{ timeInfo.absolute }}
                    </q-tooltip>
                  </template>
                </div>
                <div
                  v-if="props.row.deploymentJob?.restartRequired && rowDisplayStatus(props.row) === 'in_progress'"
                  class="text-caption text-warning"
                >
                  {{ t("workstations.deployDetailRestartRequired") }}
                </div>
                <div
                  v-if="
                    props.row.deploymentJob?.lastError &&
                    (rowDisplayStatus(props.row) === 'failed' || rowDisplayStatus(props.row) === 'timed_out')
                  "
                  class="text-caption text-negative ellipsis-2-lines"
                >
                  {{ props.row.deploymentJob.lastError }}
                </div>
                <div
                  v-else-if="props.row.deployAgent?.note && !canDeployRow(props.row) && !isActiveDeployJob(props.row)"
                  class="text-caption text-grey-7 ellipsis-2-lines"
                >
                  {{ props.row.deployAgent.note }}
                </div>
              </div>
            </q-td>
          </template>
          <template #body-cell-actions="props">
            <q-td :props="props" class="qka-ws-actions-cell" @click.stop>
              <div class="row no-wrap items-center justify-end">
                <q-btn
                  dense
                  flat
                  round
                  color="primary"
                  icon="more_vert"
                  class="qka-ctrl-actions-trigger"
                  aria-label="Actions"
                >
                  <q-menu anchor="bottom right" self="top right" class="qka-ctrl-menu" auto-close>
                    <q-list dense>
                      <q-item v-close-popup clickable @click="openDetails(props.row)">
                        <q-item-section avatar>
                          <q-icon name="monitor" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.details") }}</q-item-section>
                      </q-item>
                      <q-item
                        v-close-popup
                        clickable
                        :disable="probingId === props.row.id"
                        @click="pingOne(props.row)"
                      >
                        <q-item-section avatar>
                          <q-icon name="wifi_tethering" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.ping") }}</q-item-section>
                      </q-item>
                      <q-item v-close-popup clickable @click="openAssign(props.row)">
                        <q-item-section avatar>
                          <q-icon name="person_add" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.assign") }}</q-item-section>
                      </q-item>
                      <q-item
                        v-if="props.row.desktopDeviceId"
                        v-close-popup
                        clickable
                        :to="{ name: 'employee-computers', query: { deviceId: props.row.desktopDeviceId } }"
                      >
                        <q-item-section avatar>
                          <q-icon name="devices" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.openAgent") }}</q-item-section>
                      </q-item>
                      <q-separator
                        v-if="
                          canAddPilot(props.row) ||
                          canRemovePilot(props.row) ||
                          canDeployRow(props.row) ||
                          canCancelJob(props.row) ||
                          canRetryJob(props.row)
                        "
                      />
                      <q-item
                        v-if="canAddPilot(props.row)"
                        v-close-popup
                        clickable
                        :disable="pilotBusy"
                        @click="openPilotMembership('add', [props.row])"
                      >
                        <q-item-section avatar>
                          <q-icon name="person_add_alt" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.pilotAdd") }}</q-item-section>
                      </q-item>
                      <q-item
                        v-if="canRemovePilot(props.row)"
                        v-close-popup
                        clickable
                        :disable="pilotBusy"
                        @click="openPilotMembership('remove', [props.row])"
                      >
                        <q-item-section avatar>
                          <q-icon name="person_remove" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.pilotRemove") }}</q-item-section>
                      </q-item>
                      <q-item
                        v-if="canDeployRow(props.row)"
                        v-close-popup
                        clickable
                        :disable="deploying"
                        @click="deploySingle(props.row)"
                      >
                        <q-item-section avatar>
                          <q-icon name="system_update_alt" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.deployAgent") }}</q-item-section>
                      </q-item>
                      <q-item
                        v-if="canCancelJob(props.row)"
                        v-close-popup
                        clickable
                        class="qka-ctrl-menu__danger"
                        :disable="jobBusyId === props.row.deploymentJob?.id"
                        @click="cancelDeploy(props.row)"
                      >
                        <q-item-section avatar>
                          <q-icon name="block" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.cancelDeploy") }}</q-item-section>
                      </q-item>
                      <q-item
                        v-if="canRetryJob(props.row)"
                        v-close-popup
                        clickable
                        :disable="isActiveDeployJob(props.row) || deploying || jobBusyId === props.row.deploymentJob?.id"
                        @click="retryDeploy(props.row)"
                      >
                        <q-item-section avatar>
                          <q-icon name="refresh" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("workstations.retryDeploy") }}</q-item-section>
                      </q-item>
                    </q-list>
                  </q-menu>
                </q-btn>
              </div>
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
          <q-item><q-item-section><q-item-label caption>{{ t("workstations.colAgent") }}</q-item-label><q-item-label>{{ agentDisplayLabel(detailsRow) }}</q-item-label></q-item-section></q-item>
          <q-item><q-item-section><q-item-label caption>{{ t("workstations.colAgentVersion") }}</q-item-label><q-item-label>{{ detailsRow.agentVersion || "—" }}</q-item-label></q-item-section></q-item>
          <q-item><q-item-section><q-item-label caption>{{ t("workstations.colHeartbeat") }}</q-item-label><q-item-label>{{ detailsRow.lastHeartbeatAt || "—" }}</q-item-label></q-item-section></q-item>
          <q-item>
            <q-item-section>
              <q-item-label caption>{{ t("workstations.colDeployStatus") }}</q-item-label>
              <q-item-label>{{ primaryDeployLabel(detailsRow) }}</q-item-label>
              <q-item-label v-if="deployHistoryCaption(detailsRow)" caption>{{ deployHistoryCaption(detailsRow) }}</q-item-label>
              <q-item-label caption>{{ t("workstations.deployMethodValue") }}</q-item-label>
            </q-item-section>
          </q-item>
          <q-item v-if="detailsRow.deploymentJob">
            <q-item-section>
              <q-item-label caption>{{ t("workstations.deployHistory") }}</q-item-label>
              <q-item-label caption>{{ t("workstations.deployDetailRequested") }}: {{ detailsRow.deploymentJob.scheduledAt || detailsRow.deploymentJob.createdAt || "—" }}</q-item-label>
              <q-item-label caption>{{ t("workstations.deployDetailStarted") }}: {{ detailsRow.deploymentJob.installStartedAt || "—" }}</q-item-label>
              <q-item-label caption>{{ t("workstations.deployDetailMsiCompleted") }}: {{ detailsRow.deploymentJob.msiCompletedAt || "—" }}</q-item-label>
              <q-item-label caption>{{ t("workstations.deployDetailInstalled") }}: {{ detailsRow.deploymentJob.installedAt || "—" }}</q-item-label>
              <q-item-label caption>{{ t("workstations.deployDetailLastResult") }}: {{ detailsRow.deploymentJob.lastResult || detailsRow.deploymentJob.status || "—" }}</q-item-label>
              <q-item-label caption>{{ t("workstations.colAgentVersion") }}: {{ detailsRow.agentVersion || "—" }}</q-item-label>
              <q-item-label caption>{{ t("workstations.colHeartbeat") }}: {{ detailsRow.lastHeartbeatAt || "—" }}</q-item-label>
              <q-item-label caption>attempts: {{ detailsRow.deploymentJob.attemptCount }}</q-item-label>
              <q-item-label v-if="detailsRow.deploymentJob.restartRequired" class="text-warning" caption>
                {{ t("workstations.deployDetailRestartRequired") }}
              </q-item-label>
              <q-item-label v-if="detailsRow.deploymentJob.lastError" class="text-negative">{{ detailsRow.deploymentJob.lastError }}</q-item-label>
            </q-item-section>
          </q-item>
          <q-item>
            <q-item-section>
              <q-item-label caption>{{ t("workstations.pilotGroupLabel") }}</q-item-label>
              <q-item-label>{{ pilotMembershipLabel(detailsRow) }} ({{ detailsRow.deployAgent?.pilotGroup || "—" }})</q-item-label>
              <q-item-label caption>{{ detailsRow.deployAgent?.note }}</q-item-label>
              <q-item-label v-if="detailsRow.deployAgent?.gpoReadinessNote" caption class="text-warning">
                {{ detailsRow.deployAgent.gpoReadinessNote }}
              </q-item-label>
            </q-item-section>
          </q-item>
        </q-list>
        <div class="q-mt-md row q-gutter-sm">
          <q-btn
            v-if="canAddPilot(detailsRow)"
            color="primary"
            outline
            :label="t('workstations.pilotAdd')"
            :loading="pilotBusy"
            @click="openPilotMembership('add', [detailsRow])"
          />
          <q-btn
            v-if="canRemovePilot(detailsRow)"
            color="warning"
            outline
            :label="t('workstations.pilotRemove')"
            :loading="pilotBusy"
            @click="openPilotMembership('remove', [detailsRow])"
          />
          <q-btn
            v-if="canDeployRow(detailsRow)"
            color="primary"
            unelevated
            :label="t('workstations.deployAgent')"
            :disable="deploying"
            @click="deploySingle(detailsRow)"
          />
          <q-btn
            v-if="canCancelJob(detailsRow)"
            outline
            color="negative"
            :label="t('workstations.cancelDeploy')"
            :loading="jobBusyId === detailsRow.deploymentJob?.id"
            @click="cancelDeploy(detailsRow)"
          />
          <q-btn
            v-if="canRetryJob(detailsRow)"
            outline
            color="primary"
            :label="t('workstations.retryDeploy')"
            :loading="jobBusyId === detailsRow.deploymentJob?.id"
            :disable="deploying"
            @click="retryDeploy(detailsRow)"
          />
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

    <q-dialog v-model="pilotOpen" persistent>
      <q-card style="min-width: 480px; max-width: 640px">
        <q-card-section class="text-h6">
          {{ pilotOperation === "add" ? t("workstations.pilotAddTitle") : t("workstations.pilotRemoveTitle") }}
        </q-card-section>
        <q-card-section>
          <div class="text-body2 q-mb-sm">
            {{
              pilotOperation === "add"
                ? t("workstations.pilotAddHint", { group: pilotTargets[0]?.deployAgent?.pilotGroup || "QKA-Agent-Pilot-Computers", n: pilotTargets.length })
                : t("workstations.pilotRemoveHint", { group: pilotTargets[0]?.deployAgent?.pilotGroup || "QKA-Agent-Pilot-Computers", n: pilotTargets.length })
            }}
          </div>
          <div v-if="pilotOperation === 'add'" class="text-caption text-grey-7 q-mb-md">
            {{ t("workstations.pilotAddNoInstall") }}
          </div>
          <div v-if="pilotOperation === 'remove'" class="text-caption text-warning q-mb-md">
            {{ t("workstations.pilotRemoveNoUninstall") }}
          </div>
          <q-list bordered dense class="rounded-borders">
            <q-item v-for="row in pilotTargets" :key="row.id">
              <q-item-section>
                <q-item-label>{{ row.computerName }}</q-item-label>
                <q-item-label caption>
                  {{ t("workstations.pilotAdAccount") }}: {{ (row.computerName || "").replace(/\$$/, "") }}$
                </q-item-label>
                <q-item-label caption>SID: {{ row.objectSid || "—" }}</q-item-label>
                <q-item-label v-if="pilotOperation === 'remove' && (row.desktopDeviceId || isActiveDeployJob(row))" caption class="text-warning">
                  {{
                    isActiveDeployJob(row)
                      ? t("workstations.pilotRemoveBlockedActive")
                      : t("workstations.pilotRemoveWarnAgent")
                  }}
                </q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
          <div class="text-caption q-mt-sm">
            {{ t("workstations.pilotTargetGroup") }}:
            <strong>{{ pilotTargets[0]?.deployAgent?.pilotGroup || "QKA-Agent-Pilot-Computers" }}</strong>
          </div>
          <div v-if="pilotResultNote" class="q-mt-md text-body2" :class="pilotResultOk ? 'text-positive' : 'text-negative'">
            {{ pilotResultNote }}
          </div>
          <q-list v-if="pilotResultLines.length" dense class="q-mt-sm">
            <q-item v-for="(line, i) in pilotResultLines" :key="i">
              <q-item-section>
                <q-item-label caption>{{ line }}</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn
            flat
            :label="t(`workstations.${pilotDialogDismissKey(pilotPhase)}`)"
            @click="closePilotMembershipDialog"
          />
          <q-btn
            v-if="pilotDialogShowConfirmAction(pilotPhase)"
            color="primary"
            unelevated
            :loading="pilotBusy"
            :disable="pilotOperation === 'remove' && pilotTargets.some((r) => isActiveDeployJob(r))"
            :label="pilotOperation === 'add' ? t('workstations.pilotConfirmAdd') : t('workstations.pilotConfirmRemove')"
            @click="confirmPilotMembership"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <q-dialog v-model="deployOpen" persistent @hide="onDeployDialogHide">
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
            :disable="deployPhase !== 'idle'"
            :label="t('workstations.deployConfirmLabel', { phrase: deployPrereqs?.confirmationPhrase || 'DEPLOY' })"
            :placeholder="deployPrereqs?.confirmationPhrase || 'DEPLOY'"
            autocomplete="off"
            spellcheck="false"
          />
          <div class="text-caption text-grey-7 q-mt-sm">
            {{ t("workstations.deployConfirmLabel", { phrase: deployPrereqs?.confirmationPhrase || "DEPLOY" }) }}
            — {{ t("workstations.deployMethodValue") }}
          </div>

          <div v-if="deployPhase !== 'idle'" class="q-mt-md">
            <div class="row items-center q-mb-xs">
              <div class="col text-body2">{{ deployProgressLabel }}</div>
              <div class="col-auto text-weight-medium">{{ deployProgress }}%</div>
            </div>
            <q-linear-progress
              size="10px"
              rounded
              :value="deployProgress / 100"
              :color="deployPhase === 'error' ? 'negative' : deployPhase === 'done' ? 'positive' : 'primary'"
              track-color="grey-3"
            />
            <div v-if="deployPhase === 'done'" class="text-positive text-caption q-mt-sm">
              {{ deployFinishNote || t("workstations.deployScheduledNote") }}
            </div>
            <div v-if="deployPhase === 'error'" class="text-negative text-caption q-mt-sm">
              {{ deployFinishNote || t("workstations.deployError") }}
            </div>
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn
            v-if="deployPhase === 'idle'"
            flat
            :label="t('workstations.cancel')"
            @click="closeDeployDialog"
          />
          <q-btn
            v-if="deployPhase === 'done' || deployPhase === 'error'"
            color="primary"
            unelevated
            :label="t('workstations.deployClose')"
            @click="closeDeployDialog"
          />
          <q-btn
            v-if="deployPhase === 'idle' || deployPhase === 'running'"
            color="negative"
            unelevated
            :loading="deploying"
            :disable="deployPhase === 'running' || deployConfirmation !== (deployPrereqs?.confirmationPhrase || 'DEPLOY')"
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
import {
  pilotDialogDismissKey,
  pilotDialogPhaseAfterAttempt,
  pilotDialogShowConfirmAction,
  type PilotDialogPhase,
} from "@/pilotMembershipDialog";

const VIEW_KEY = "qka.workstations.view.v4";
const ACTIVE_DEPLOY_STATUSES = new Set(["pending", "in_progress"]);
const ACTIVE_JOB_RAW_STATUSES = new Set([
  "requested",
  "approved",
  "scheduled",
  "waiting_for_computer",
  "installing",
]);
const TERMINAL_HISTORY_STATUSES = new Set(["failed", "cancelled", "timed_out"]);
const DEPLOY_POLL_MS = 8000;
const RELATIVE_TICK_MS = 30000;
const { t, locale } = useI18n();
/** Ticked so relative deploy timestamps refresh without a full table reload. */
const relativeNowMs = ref(Date.now());
let relativeTickTimer: ReturnType<typeof setInterval> | null = null;
const section = ref("workstations");
const loading = ref(false);
const backgroundRefreshing = ref(false);
/** Suppress filter watchers / QTable @request until the first intentional load finishes. */
const viewHydrated = ref(false);
let filterWatchEnabled = false;
let deployPollTimer: ReturnType<typeof setInterval> | null = null;
const syncing = ref(false);
const probing = ref(false);
const probingId = ref<string | null>(null);
const busy = ref(false);
const deploying = ref(false);
const deployPhase = ref<"idle" | "running" | "done" | "error">("idle");
const deployProgress = ref(0);
const deployProgressLabel = ref("");
const deployFinishNote = ref("");
const pendingTokenJobs = ref<Array<{ id: string; computerName: string; machineEnrollmentToken: string }>>([]);
const pendingTokenInstruction = ref("");
let deployProgressTimer: ReturnType<typeof setInterval> | null = null;
const jobBusyId = ref<string | null>(null);
const pilotBusy = ref(false);
const pilotOpen = ref(false);
const pilotOperation = ref<"add" | "remove">("add");
const pilotTargets = ref<Workstation[]>([]);
const pilotPhase = ref<PilotDialogPhase>("confirm");
const pilotResultNote = ref("");
const pilotResultOk = ref(false);
const pilotResultLines = ref<string[]>([]);
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
  "pilotMembership",
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
    name: "pilotMembership",
    label: t("workstations.colPilot"),
    field: (r: Workstation) => pilotMembershipLabel(r),
    align: "left" as const,
    required: false,
  },
  {
    name: "deployStatus",
    label: t("workstations.colDeployStatus"),
    field: (r: Workstation) => primaryDeployLabel(r),
    align: "left" as const,
    required: true,
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
  { label: t("workstations.deployStatus.installing"), value: "installing" },
  { label: t("workstations.deployStatus.failed"), value: "failed" },
  { label: t("workstations.deployStatus.cancelled"), value: "cancelled" },
];

function wsFilterDisplay(
  value: string | null | undefined,
  options: Array<{ label: string; value: string }>,
  emptyLabel: string,
): string {
  if (!value) return emptyLabel;
  return options.find((o) => o.value === value)?.label || emptyLabel;
}

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
  if (deploying.value) return false;
  return selectedRows.value.every((r) => canDeployRow(r));
});
const deployDisabledReason = computed(() => {
  if (!deployPrereqs.value?.deployEnabled) {
    return (deployPrereqs.value?.blockers || []).join(" ") || t("workstations.deployNote");
  }
  if (!selectedRows.value.length) return t("workstations.deploySelectHint");
  if (selectedRows.value.some((r) => isActiveDeployJob(r))) {
    return t("workstations.deployActiveJobHint");
  }
  const blocked = selectedRows.value.filter((r) => !canDeployRow(r));
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

function rowDisplayStatus(row: Workstation) {
  const fromJob = (row.deploymentJob?.displayStatus || "").trim();
  if (fromJob === "pending") return "in_progress"; // legacy API value → In Progress
  if (fromJob) return fromJob;
  const raw = (row.deploymentJob?.status || row.deployAgent?.status || "").trim();
  if (ACTIVE_JOB_RAW_STATUSES.has(raw) || raw === "installing") {
    return "in_progress";
  }
  if (row.agentStatus === "connected") return "online";
  if (row.agentStatus === "registered_offline") {
    return row.lastHeartbeatAt ? "offline" : "installed";
  }
  if (row.deployAgent?.enabled) return "ready";
  if (TERMINAL_HISTORY_STATUSES.has(raw)) return raw;
  return raw || "none";
}

function isActiveDeployJob(row: Workstation) {
  const display = rowDisplayStatus(row);
  if (ACTIVE_DEPLOY_STATUSES.has(display)) return true;
  return ACTIVE_JOB_RAW_STATUSES.has(row.deploymentJob?.status || "");
}

function canDeployRow(row: Workstation) {
  if (!deployPrereqs.value?.deployEnabled) return false;
  if (row.desktopDeviceId || row.agentStatus === "connected" || row.agentStatus === "registered_offline") {
    return false;
  }
  if (isActiveDeployJob(row)) return false;
  return Boolean(row.deployAgent?.enabled);
}

function canAddPilot(row: Workstation) {
  if (!row.isPresent || !row.accountEnabled || row.isServerOrDc) return false;
  if (!row.objectSid) return false;
  return row.deployAgent?.pilotGroupMember !== true;
}

function canRemovePilot(row: Workstation) {
  if (row.deployAgent?.pilotGroupMember !== true) return false;
  // UI still shows the action; confirm dialog + API block active deployments.
  return true;
}

const canBulkAddPilot = computed(() => selectedRows.value.some((r) => canAddPilot(r)));
const canBulkRemovePilot = computed(() => selectedRows.value.some((r) => canRemovePilot(r)));

function pilotMembershipLabel(row: Workstation) {
  // Pilot membership is independent of deployment job lifecycle.
  if (pilotBusy.value && pilotTargets.value.some((t) => t.id === row.id)) {
    return t("workstations.pilotStatus.updating");
  }
  if (!row.deployAgent?.pilotGroupMember) {
    return t("workstations.pilotStatus.notInPilot");
  }
  const readiness = row.deployAgent?.taskReadiness || "";
  if (readiness === "task_available") {
    return t("workstations.pilotStatus.taskAvailable");
  }
  if (readiness === "gpo_pending" || row.deployAgent?.gpoReadinessNote) {
    return t("workstations.pilotStatus.gpoPending");
  }
  return t("workstations.pilotStatus.inPilot");
}

function pilotMembershipColor(row: Workstation) {
  if (!row.deployAgent?.pilotGroupMember) return "grey-6";
  if (row.deployAgent?.taskReadiness === "task_available") return "primary";
  if (row.deployAgent?.taskReadiness === "gpo_pending") return "warning";
  return "info";
}

function openPilotMembership(operation: "add" | "remove", rowsIn: Workstation[]) {
  const filtered =
    operation === "add" ? rowsIn.filter((r) => canAddPilot(r)) : rowsIn.filter((r) => canRemovePilot(r));
  if (!filtered.length) return;
  pilotOperation.value = operation;
  pilotTargets.value = filtered;
  pilotPhase.value = "confirm";
  pilotResultNote.value = "";
  pilotResultOk.value = false;
  pilotResultLines.value = [];
  pilotBusy.value = false;
  pilotOpen.value = true;
}

function closePilotMembershipDialog() {
  // Dismiss only — never reverses AD membership changes.
  pilotOpen.value = false;
}

async function confirmPilotMembership() {
  if (!pilotTargets.value.length || pilotBusy.value) return;
  if (!pilotDialogShowConfirmAction(pilotPhase.value)) return;
  pilotBusy.value = true;
  pilotResultNote.value = "";
  pilotResultLines.value = [];
  try {
    const res = await api.changePilotMembership({
      workstationIds: pilotTargets.value.map((r) => r.id),
      operation: pilotOperation.value,
    });
    if (res.prerequisites) deployPrereqs.value = res.prerequisites;
    const byId = new Map((res.workstations || []).map((w) => [w.id, w]));
    rows.value = rows.value.map((r) => byId.get(r.id) || r);
    selectedRows.value = selectedRows.value.map((r) => byId.get(r.id) || r);
    if (detailsRow.value && byId.has(detailsRow.value.id)) {
      detailsRow.value = byId.get(detailsRow.value.id)!;
    }
    pilotResultOk.value = Boolean(res.allSucceeded);
    pilotResultNote.value = res.partial
      ? t("workstations.pilotPartial", { ok: res.succeeded, fail: res.failed })
      : res.allSucceeded
        ? t("workstations.pilotSuccess", { n: res.succeeded })
        : t("workstations.pilotFailed");
    if (res.note) pilotResultNote.value = `${pilotResultNote.value} ${res.note}`;
    pilotResultLines.value = (res.results || []).map((r) => {
      const name = r.computerName || r.workstationId;
      const code = r.result || "error";
      const adMiss =
        code === "workstation_not_in_inventory" ||
        code === "missing" ||
        code === "no_sid" ||
        code === "disabled";
      const prefix = adMiss ? "AD/inventory" : code === "error" || code === "verify_failed" ? "AD write" : "Result";
      return `${name}: [${prefix}] ${code}${r.detail ? ` — ${r.detail}` : ""}`;
    });
    pilotPhase.value = pilotDialogPhaseAfterAttempt({
      allSucceeded: Boolean(res.allSucceeded),
      partial: Boolean(res.partial),
      failed: !res.allSucceeded,
    });
    syncDeployPolling();
  } catch (e) {
    pilotResultOk.value = false;
    if (e instanceof ApiError) {
      if (e.status === 404) {
        pilotResultNote.value = t("workstations.pilotApiNotFound");
      } else if (e.status === 403) {
        pilotResultNote.value = t("workstations.pilotApiForbidden");
      } else if (e.status === 401) {
        pilotResultNote.value = t("workstations.pilotApiUnauthorized");
      } else {
        pilotResultNote.value = t("workstations.pilotApiError", { status: e.status, detail: e.message || "—" });
      }
    } else {
      pilotResultNote.value = t("workstations.pilotError");
    }
    pilotPhase.value = pilotDialogPhaseAfterAttempt({ error: true });
  } finally {
    pilotBusy.value = false;
  }
}

function canRetryJob(row: Workstation) {
  if (isActiveDeployJob(row) || row.desktopDeviceId) return false;
  if (!row.deploymentJob?.id) return false;
  const display = rowDisplayStatus(row);
  const st = row.deploymentJob?.status;
  return (
    display === "failed" ||
    display === "cancelled" ||
    display === "timed_out" ||
    st === "failed" ||
    st === "cancelled" ||
    st === "timed_out"
  );
}

function canCancelJob(row: Workstation) {
  // Cancel only before Windows reports install started (raw status still pre-installing).
  if (!row.deploymentJob?.id) return false;
  const st = row.deploymentJob?.status || "";
  return st === "requested" || st === "approved" || st === "scheduled" || st === "waiting_for_computer";
}

function deployStatusKey(status: string | undefined | null) {
  const key = (status || "").trim();
  if (!key) return "none";
  return key;
}

function localizeDeployStatus(status: string | undefined | null) {
  const key = deployStatusKey(status);
  const path = `workstations.deployStatus.${key}`;
  const label = t(path);
  return label === path ? key : label;
}

function agentDisplayLabel(row: Workstation) {
  if (row.agentStatus === "connected") return t("workstations.agent.connected");
  if (row.agentStatus === "registered_offline") return t("workstations.agent.registered_offline");
  if (row.agentStatus === "not_registered") return t("workstations.agent.not_registered");
  return row.agentStatusLabel || "—";
}

function deployHistoryCaption(row: Workstation): string {
  const job = row.deploymentJob;
  if (!job?.status) return "";
  if (isActiveDeployJob(row)) return "";
  return t("workstations.deployHistoryCaption", { status: localizeDeployStatus(job.status) });
}

function primaryDeployLabel(row: Workstation) {
  const display = rowDisplayStatus(row);
  if (display === "ready" || (row.deployAgent?.enabled && !isActiveDeployJob(row) && !row.desktopDeviceId)) {
    if (
      !isActiveDeployJob(row) &&
      !row.desktopDeviceId &&
      (display === "none" || display === "cancelled" || display === "failed" || display === "timed_out" || display === "ready")
    ) {
      if (row.deployAgent?.enabled || canDeployRow(row)) return localizeDeployStatus("ready");
    }
  }
  if (display === "online") return localizeDeployStatus("online");
  if (display === "deployed") return localizeDeployStatus("deployed");
  if (display === "offline") return localizeDeployStatus("offline");
  if (display === "installed") return localizeDeployStatus("installed");
  if (display === "in_progress") return localizeDeployStatus("in_progress");
  if (display === "pending") return localizeDeployStatus("pending");
  if (display === "failed") return localizeDeployStatus("failed");
  if (display === "timed_out") return localizeDeployStatus("timed_out");
  if (display === "cancelled") return localizeDeployStatus("cancelled");
  if (row.deployAgent?.enabled || canDeployRow(row)) return localizeDeployStatus("ready");
  return localizeDeployStatus(display || row.deployAgent?.status || "blocked");
}

function firstValidIso(...candidates: Array<string | null | undefined>): string | null {
  for (const raw of candidates) {
    if (!raw) continue;
    const ms = Date.parse(raw);
    if (!Number.isNaN(ms)) return raw;
  }
  return null;
}

function formatAbsoluteTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  try {
    return d.toLocaleString(locale.value || undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return d.toLocaleString();
  }
}

function formatRelativeTime(iso: string, nowMs: number): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const diffSec = Math.round((nowMs - then) / 1000);
  const abs = Math.abs(diffSec);
  if (abs < 45) return t("workstations.relative.justNow");
  const minutes = Math.round(abs / 60);
  if (minutes < 60) {
    return t("workstations.relative.minutesAgo", { n: minutes });
  }
  const hours = Math.round(minutes / 60);
  if (hours < 48) {
    return t("workstations.relative.hoursAgo", { n: hours });
  }
  const days = Math.round(hours / 24);
  return t("workstations.relative.daysAgo", { n: days });
}

type DeployStatusTimeInfo = { label: string; absolute: string; iso: string };

function deployStatusTimeInfo(row: Workstation): DeployStatusTimeInfo | null {
  // Depend on tick so captions refresh in-place.
  const nowMs = relativeNowMs.value;
  const display = rowDisplayStatus(row);
  const job = row.deploymentJob;

  let iso: string | null = null;
  let kind: "plain" | "completed" | "failed" | "timed_out" | "cancelled" = "plain";

  if (display === "in_progress" || display === "pending") {
    iso = firstValidIso(job?.installStartedAt, job?.scheduledAt, job?.updatedAt, job?.createdAt);
    kind = "plain";
  } else if (display === "online" || display === "deployed" || display === "installed") {
    iso = firstValidIso(job?.installedAt, job?.msiCompletedAt, job?.updatedAt, row.lastHeartbeatAt);
    kind = "completed";
  } else if (display === "failed") {
    iso = firstValidIso(job?.updatedAt, job?.msiCompletedAt, job?.installStartedAt, job?.createdAt);
    kind = "failed";
  } else if (display === "timed_out") {
    iso = firstValidIso(job?.updatedAt, job?.scheduledAt, job?.createdAt);
    kind = "timed_out";
  } else if (display === "cancelled") {
    iso = firstValidIso(job?.cancelledAt, job?.updatedAt, job?.createdAt);
    kind = "cancelled";
  } else if (display === "offline") {
    iso = firstValidIso(row.lastHeartbeatAt, job?.updatedAt);
    kind = "plain";
  } else if (
    (display === "ready" || display === "none") &&
    job?.status &&
    TERMINAL_HISTORY_STATUSES.has(job.status)
  ) {
    // Ready again after a terminal job — show when that outcome happened.
    iso = firstValidIso(job.cancelledAt, job.updatedAt, job.installedAt, job.createdAt);
    if (job.status === "failed") kind = "failed";
    else if (job.status === "timed_out") kind = "timed_out";
    else if (job.status === "cancelled") kind = "cancelled";
    else kind = "plain";
  }

  if (!iso) return null;
  const when = formatRelativeTime(iso, nowMs);
  if (!when) return null;
  let label = when;
  if (kind === "completed") label = t("workstations.relative.completed", { when });
  else if (kind === "failed") label = t("workstations.relative.failed", { when });
  else if (kind === "timed_out") label = t("workstations.relative.timedOut", { when });
  else if (kind === "cancelled") label = t("workstations.relative.cancelled", { when });
  return { label, absolute: formatAbsoluteTime(iso), iso };
}

function startRelativeTick() {
  if (relativeTickTimer) return;
  relativeNowMs.value = Date.now();
  relativeTickTimer = setInterval(() => {
    relativeNowMs.value = Date.now();
  }, RELATIVE_TICK_MS);
}

function stopRelativeTick() {
  if (relativeTickTimer) {
    clearInterval(relativeTickTimer);
    relativeTickTimer = null;
  }
}

function deployStatusColor(status: string) {
  if (status === "in_progress") return "primary";
  if (status === "installed" || status === "online" || status === "deployed") return "positive";
  if (status === "failed" || status === "timed_out") return "negative";
  if (status === "offline") return "warning";
  if (status === "cancelled") return "grey-6";
  if (status === "pending" || status === "ready") return "grey-7";
  return "grey-6";
}

function deployStatusOutline(status: string) {
  return status === "pending" || status === "ready" || status === "cancelled" || status === "offline";
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
function wsReachChipClass(status: string) {
  if (status === "reachable") return "qka-ctrl-chip--online";
  if (status === "dns_failed" || status === "probe_error") return "qka-ctrl-chip--failed";
  if (status === "no_reply") return "qka-ctrl-chip--paused";
  return "qka-ctrl-chip--neutral";
}
function wsAgentChipClass(status: string) {
  if (status === "connected") return "qka-ctrl-chip--online";
  if (status === "registered_offline") return "qka-ctrl-chip--paused";
  if (status === "registered") return "qka-ctrl-chip--indexing";
  return "qka-ctrl-chip--neutral";
}

function stopDeployPolling() {
  if (deployPollTimer) {
    clearInterval(deployPollTimer);
    deployPollTimer = null;
  }
}

function syncDeployPolling() {
  const needsPoll = rows.value.some((r) => isActiveDeployJob(r));
  if (needsPoll) {
    if (!deployPollTimer) {
      deployPollTimer = setInterval(() => {
        void reload({ background: true });
      }, DEPLOY_POLL_MS);
    }
  } else {
    stopDeployPolling();
  }
}

function applyOptimisticJobs(
  jobs: Array<{
    id: string;
    workstationId: string;
    computerName: string;
    status: string;
    lastError: string;
  }>,
) {
  const nowIso = new Date().toISOString();
  const byWs = new Map(jobs.map((j) => [j.workstationId, j]));
  const patchRow = (row: Workstation) => {
    const job = byWs.get(row.id);
    if (!job) return row;
    const status = job.status || "scheduled";
    return {
      ...row,
      deployAgent: {
        ...row.deployAgent,
        enabled: false,
        status,
        note: t("workstations.deployScheduledNote"),
      },
      deploymentJob: {
        id: job.id,
        status,
        statusCode: 0,
        displayStatus: "in_progress",
        attemptCount: row.deploymentJob?.attemptCount || 1,
        lastError: job.lastError || "",
        lastResult: "scheduled",
        msiVersion: row.deploymentJob?.msiVersion || "",
        msiExitCode: null,
        restartRequired: false,
        engine: row.deploymentJob?.engine || "gpo",
        enrollmentMode: row.deploymentJob?.enrollmentMode || "machine_identity",
        desktopDeviceId: row.deploymentJob?.desktopDeviceId || null,
        createdAt: row.deploymentJob?.createdAt || nowIso,
        updatedAt: nowIso,
        approvedAt: nowIso,
        scheduledAt: nowIso,
        installStartedAt: null,
        msiCompletedAt: null,
        installedAt: null,
        cancelledAt: null,
      },
    };
  };
  rows.value = rows.value.map(patchRow);
  selectedRows.value = selectedRows.value.map(patchRow);
  if (detailsRow.value) {
    detailsRow.value = patchRow(detailsRow.value);
  }
  syncDeployPolling();
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
    syncDeployPolling();
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
function onOuTicked(next: readonly string[]) {
  tickedOuDns.value = [...next];
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

function stopDeployProgressTick() {
  if (deployProgressTimer) {
    clearInterval(deployProgressTimer);
    deployProgressTimer = null;
  }
}

function resetDeployDialogState() {
  stopDeployProgressTick();
  deploying.value = false;
  deployPhase.value = "idle";
  deployProgress.value = 0;
  deployProgressLabel.value = "";
  deployFinishNote.value = "";
  deployConfirmation.value = "";
  pendingTokenJobs.value = [];
  pendingTokenInstruction.value = "";
}

function openDeploy() {
  if (!canDeploySelected.value || deploying.value || deployPhase.value === "running") return;
  resetDeployDialogState();
  deployOpen.value = true;
}

function deploySingle(row: Workstation) {
  if (!canDeployRow(row) || deploying.value) return;
  selectedRows.value = [row];
  openDeploy();
}

function closeDeployDialog() {
  if (deployPhase.value === "running") return;
  const openTokens = pendingTokenJobs.value.length > 0;
  const jobs = [...pendingTokenJobs.value];
  const instruction = pendingTokenInstruction.value;
  deployOpen.value = false;
  resetDeployDialogState();
  if (openTokens) {
    tokenJobs.value = jobs;
    tokenInstruction.value = instruction || t("workstations.tokenHint");
    tokenOpen.value = true;
  }
}

function onDeployDialogHide() {
  if (deployPhase.value === "running") {
    // Persistent dialog should not hide mid-run; keep state if somehow closed.
    return;
  }
  if (!deployOpen.value && deployPhase.value !== "idle") {
    resetDeployDialogState();
  }
}

async function confirmDeploy() {
  if (!canDeploySelected.value || !deployPrereqs.value) return;
  if (deployPhase.value === "running" || deploying.value) return;
  // Block duplicate submits while an active job already exists for any selection.
  if (selectedRows.value.some((r) => isActiveDeployJob(r))) {
    error.value = t("workstations.deployActiveJobHint");
    return;
  }
  deploying.value = true;
  deployPhase.value = "running";
  deployProgress.value = 5;
  deployProgressLabel.value = t("workstations.deployProgressValidating");
  deployFinishNote.value = "";
  error.value = "";
  pendingTokenJobs.value = [];
  pendingTokenInstruction.value = "";
  stopDeployProgressTick();
  deployProgressTimer = setInterval(() => {
    if (deployPhase.value !== "running") return;
    if (deployProgress.value < 15) {
      deployProgress.value = Math.min(15, deployProgress.value + 2);
      deployProgressLabel.value = t("workstations.deployProgressValidating");
    } else if (deployProgress.value < 70) {
      deployProgress.value = Math.min(70, deployProgress.value + 2);
      deployProgressLabel.value = t("workstations.deployProgressAuthorizing");
    }
  }, 180);
  try {
    const result = await api.deployWorkstationAgents({
      workstationIds: selectedRows.value.map((r) => r.id),
      confirmation: deployConfirmation.value,
      approve: true,
    });
    stopDeployProgressTick();
    deployProgress.value = 82;
    deployProgressLabel.value = t("workstations.deployProgressRefreshing");
    // Immediate UI: Scheduled / Awaiting Windows Task (no full-page reload).
    applyOptimisticJobs(result.jobs || []);
    syncNote.value = result.note || t("workstations.deployScheduledNote");
    const staged = (result.jobs || []).filter((j) => (j as { tokenStaged?: boolean }).tokenStaged);
    const withTokens = (result.jobs || [])
      .filter((j) => j.machineEnrollmentToken)
      .map((j) => ({
        id: j.id,
        computerName: j.computerName,
        machineEnrollmentToken: String(j.machineEnrollmentToken),
      }));
    if (staged.length && !withTokens.length) {
      syncNote.value = `${t("workstations.deployScheduledNote")} ${result.tokenProvisioning?.instruction || t("workstations.tokenAutoStaged")}`;
    } else if (withTokens.length) {
      pendingTokenJobs.value = withTokens;
      pendingTokenInstruction.value = result.tokenProvisioning?.instruction || t("workstations.tokenHint");
    }
    await reload({ background: true });
    await loadDeployPrereqs();
    syncDeployPolling();
    deployProgress.value = 100;
    deployProgressLabel.value = t("workstations.deployProgressDone");
    deployFinishNote.value = syncNote.value || t("workstations.deployScheduledNote");
    deployPhase.value = "done";
  } catch (err) {
    stopDeployProgressTick();
    const msg = formatApiFailure(err, t("workstations.deployError"));
    error.value = msg;
    deployFinishNote.value = msg;
    deployProgressLabel.value = t("workstations.deployError");
    deployProgress.value = Math.max(deployProgress.value, 100);
    deployPhase.value = "error";
  } finally {
    stopDeployProgressTick();
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
  if (!jobId || !canCancelJob(row)) return;
  jobBusyId.value = jobId;
  error.value = "";
  try {
    await api.cancelWorkstationDeploy(jobId);
    await reload({ background: true });
    syncDeployPolling();
  } catch (err) {
    error.value = formatApiFailure(err, t("workstations.deployError"));
  } finally {
    jobBusyId.value = null;
  }
}

async function retryDeploy(row: Workstation) {
  const jobId = row.deploymentJob?.id;
  if (!jobId || !canRetryJob(row) || isActiveDeployJob(row)) return;
  jobBusyId.value = jobId;
  error.value = "";
  try {
    const result = await api.retryWorkstationDeploy(jobId);
    applyOptimisticJobs([
      {
        id: result.id || jobId,
        workstationId: row.id,
        computerName: row.computerName,
        status: result.status || "scheduled",
        lastError: "",
      },
    ]);
    syncNote.value = t("workstations.deployScheduledNote");
    await reload({ background: true });
    await loadDeployPrereqs();
    syncDeployPolling();
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
  stopDeployPolling();
  stopDeployProgressTick();
  stopRelativeTick();
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
  syncDeployPolling();
  startRelativeTick();
});
</script>

<style scoped>
.qka-ws-deploy-status {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
  max-width: 100%;
}
.qka-ws-deploy-status__line {
  display: flex;
  align-items: center;
  min-width: 0;
  max-width: 100%;
}
.qka-ws-deploy-status__chip {
  max-width: 100%;
}
.qka-ws-deploy-status__time {
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
  cursor: default;
}
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
.qka-ws-table :deep(table) {
  border-collapse: separate;
  border-spacing: 0;
}
.qka-ws-table :deep(th:last-child),
.qka-ws-table :deep(td:last-child) {
  position: sticky;
  right: 0;
  z-index: 2;
  background: var(--q-card-bg, #fff);
  box-shadow: -4px 0 6px -6px rgba(16, 32, 51, 0.12);
}
.body--dark .qka-ws-table :deep(th:last-child),
.body--dark .qka-ws-table :deep(td:last-child) {
  background: var(--q-dark-page, #1d1d1d);
  box-shadow: -4px 0 6px -6px rgba(0, 0, 0, 0.35);
}
.qka-ws-actions-cell {
  min-width: 48px;
  width: 56px;
  white-space: nowrap;
}
.qka-ws-drawer .text-wrap {
  word-break: break-all;
  white-space: normal;
}
.qka-ws-deploy-action {
  border: 1px solid rgba(255, 255, 255, 0.45);
}
.qka-ws-actionbar {
  position: sticky;
  top: 0;
  z-index: 3;
}
.body--dark .qka-ws-readiness :deep(.q-item) {
  color: inherit;
}
</style>
