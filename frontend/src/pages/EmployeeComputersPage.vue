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

    <div class="qka-ctrl-toolbar">
      <q-input
        v-model="deviceSearch"
        class="qka-ctrl-field qka-ctrl-search"
        dense
        outlined
        clearable
        hide-bottom-space
        debounce="200"
        :placeholder="t('desktop.searchDevicesHint')"
        :aria-label="t('desktop.searchDevices')"
        @update:model-value="onFilterChange"
      >
        <template #prepend>
          <q-icon name="search" size="16px" />
        </template>
      </q-input>

      <q-select
        v-model="filterUserId"
        class="qka-ctrl-field qka-ctrl-select--employee"
        :options="filteredEmployeeOptions"
        emit-value
        map-options
        clearable
        use-input
        fill-input
        hide-selected
        hide-bottom-space
        input-debounce="0"
        outlined
        dense
        options-dense
        autocomplete="off"
        :placeholder="t('desktop.filterAllEmployees')"
        :aria-label="t('desktop.filterEmployee')"
        popup-content-class="qka-ctrl-menu"
        @filter="onEmployeeFilter"
        @focus="onFilterInputFocus"
        @update:model-value="onFilterChange"
      >
        <template #prepend>
          <q-icon name="group" size="16px" />
        </template>
        <template #option="scope">
          <q-item v-bind="scope.itemProps" :class="{ 'qka-ctrl-menu__active': scope.selected }">
            <q-item-section avatar>
              <q-icon
                :name="scope.opt.value === '__unassigned__' ? 'person_off' : scope.opt.value ? 'person' : 'group'"
                size="16px"
              />
            </q-item-section>
            <q-item-section>
              <q-item-label>{{ scope.opt.label }}</q-item-label>
              <q-item-label v-if="scope.opt.caption" caption>{{ scope.opt.caption }}</q-item-label>
            </q-item-section>
          </q-item>
        </template>
      </q-select>

      <q-select
        v-model="filterAgentStatus"
        class="qka-ctrl-field qka-ctrl-select--compact"
        :options="agentStatusFilterOptions"
        emit-value
        map-options
        clearable
        hide-bottom-space
        outlined
        dense
        options-dense
        :display-value="agentStatusFilterDisplay"
        :aria-label="t('desktop.filterAgentStatus')"
        popup-content-class="qka-ctrl-menu"
        @update:model-value="onFilterChange"
      >
        <template #prepend>
          <q-icon name="devices" size="16px" />
        </template>
        <template #option="scope">
          <q-item v-bind="scope.itemProps" :class="{ 'qka-ctrl-menu__active': scope.selected }">
            <q-item-section avatar>
              <q-icon :name="scope.opt.icon || 'devices'" size="16px" />
            </q-item-section>
            <q-item-section>
              <q-item-label>{{ scope.opt.label }}</q-item-label>
            </q-item-section>
          </q-item>
        </template>
      </q-select>

      <q-select
        v-model="filterIndexing"
        class="qka-ctrl-field qka-ctrl-select--compact"
        :options="indexingFilterOptions"
        emit-value
        map-options
        clearable
        hide-bottom-space
        outlined
        dense
        options-dense
        :display-value="indexingFilterDisplay"
        :aria-label="t('desktop.filterIndexing')"
        popup-content-class="qka-ctrl-menu"
        @update:model-value="onFilterChange"
      >
        <template #prepend>
          <q-icon name="storage" size="16px" />
        </template>
        <template #option="scope">
          <q-item v-bind="scope.itemProps" :class="{ 'qka-ctrl-menu__active': scope.selected }">
            <q-item-section avatar>
              <q-icon :name="scope.opt.icon || 'storage'" size="16px" />
            </q-item-section>
            <q-item-section>
              <q-item-label>{{ scope.opt.label }}</q-item-label>
            </q-item-section>
          </q-item>
        </template>
      </q-select>
    </div>

    <q-expansion-item
      dense
      dense-toggle
      class="q-mb-md"
      header-class="text-caption text-grey-7"
      :label="t('desktop.advancedRegistration')"
    >
      <div class="q-pa-sm">
        <div class="text-caption text-grey-7 q-mb-sm">{{ t("desktop.advancedRegistrationHint") }}</div>
        <q-btn
          dense
          outline
          color="primary"
          :loading="busy"
          :label="t('desktop.issueDeviceEnrollment')"
          @click="issueDeviceEnrollment"
        >
          <q-tooltip>{{ t("desktop.issueDeviceEnrollmentHint") }}</q-tooltip>
        </q-btn>
        <q-banner v-if="enrollmentCode" class="bg-grey-2 q-mt-md">
          <div class="text-subtitle2">{{ t("desktop.enrollmentIssued") }}</div>
          <div class="text-caption">{{ t("desktop.enrollmentHint") }}</div>
          <code class="text-body2">{{ enrollmentCode }}</code>
          <div v-if="enrollmentExpires" class="text-caption q-mt-xs">{{ t("desktop.expires") }}: {{ enrollmentExpires }}</div>
        </q-banner>
      </div>
    </q-expansion-item>

    <q-card bordered>
      <q-card-section>
        <q-table
          flat
          dense
          binary-state-sort
          row-key="id"
          :rows="devices"
          :columns="columns"
          :loading="loading"
          selection="single"
          v-model:selected="selectedRows"
          v-model:pagination="pagination"
          :rows-per-page-options="[10, 25, 50]"
          class="qka-agent-devices-table qka-ctrl-table"
        >
          <template #body-cell-computerName="props">
            <q-td :props="props">
              <div class="qka-ctrl-table__primary">{{ deviceHostname(props.row) }}</div>
            </q-td>
          </template>
          <template #body-cell-employeeName="props">
            <q-td :props="props">
              <span
                class="qka-ctrl-table__secondary"
                :class="props.row.employeeAssigned === false ? 'text-warning' : ''"
              >
                {{
                  props.row.employeeAssigned === false
                    ? t("desktop.employeeUnassigned")
                    : props.row.employeeName || "—"
                }}
              </span>
            </q-td>
          </template>
          <template #body-cell-agentStatus="props">
            <q-td :props="props">
              <q-chip
                dense
                class="qka-ctrl-chip"
                :class="props.row.online ? 'qka-ctrl-chip--online' : 'qka-ctrl-chip--offline'"
                :icon="props.row.online ? 'wifi' : 'wifi_off'"
                :label="props.row.online ? t('desktop.connected') : t('desktop.offline')"
              />
            </q-td>
          </template>
          <template #body-cell-agentVersion="props">
            <q-td :props="props">
              <div class="row no-wrap items-center q-gutter-xs">
                <span class="text-weight-medium">{{ formatAgentVersion(props.row.agentVersion) }}</span>
                <q-chip
                  v-if="isUpgradeInProgress(props.row)"
                  dense
                  class="qka-ctrl-chip qka-ctrl-chip--indexing"
                  icon="system_update"
                  :label="t('desktop.upgrading')"
                />
                <q-chip
                  v-else-if="canUpgradeDevice(props.row)"
                  dense
                  clickable
                  class="qka-ctrl-chip qka-ctrl-chip--paused"
                  :disable="busy || upgradingId === props.row.id"
                  :label="t('desktop.upgradeAgent')"
                  @click.stop="upgradeAgent(props.row)"
                >
                  <q-tooltip>
                    {{
                      t("desktop.upgradeTooltip", {
                        from: formatAgentVersion(props.row.agentVersion),
                        to: formatAgentVersion(props.row.availableAgentVersion),
                      })
                    }}
                  </q-tooltip>
                </q-chip>
              </div>
            </q-td>
          </template>
          <template #body-cell-indexed="props">
            <q-td :props="props">
              {{ props.row.filesIndexed.toLocaleString() }} {{ t("desktop.files") }}
            </q-td>
          </template>
          <template #body-cell-indexing="props">
            <q-td :props="props">
              <div class="qka-indexing-cell">
                <q-chip
                  dense
                  class="qka-ctrl-chip"
                  :class="indexingChipClass(props.row)"
                  :label="indexingUi(props.row).label"
                >
                  <q-tooltip v-if="indexingUi(props.row).tooltip">{{ indexingUi(props.row).tooltip }}</q-tooltip>
                </q-chip>
                <div class="text-caption text-grey-7 qka-indexing-secondary">
                  {{ indexingUi(props.row).secondary }}
                </div>
                <q-btn
                  v-if="indexingUi(props.row).action"
                  dense
                  flat
                  color="primary"
                  class="q-px-none"
                  size="sm"
                  :loading="busy && commandDeviceId === props.row.id"
                  :disable="busy"
                  :label="indexingUi(props.row).action!.label"
                  @click.stop="runIndexingAction(props.row)"
                />
              </div>
            </q-td>
          </template>
          <template #body-cell-actions="props">
            <q-td :props="props" class="qka-agent-actions-cell" @click.stop>
              <div class="row no-wrap items-center justify-end">
                <q-btn
                  dense
                  flat
                  round
                  color="primary"
                  icon="more_vert"
                  class="qka-ctrl-actions-trigger"
                  :aria-label="t('desktop.actions')"
                >
                  <q-menu anchor="bottom right" self="top right" class="qka-ctrl-menu">
                    <q-list dense>
                      <q-item v-close-popup clickable @click="openComputerDetails(props.row)">
                        <q-item-section avatar>
                          <q-icon name="monitor" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("desktop.computerDetails") }}</q-item-section>
                      </q-item>
                      <q-item
                        clickable
                        :disable="browseBusy || (!canBrowseDevice(props.row) && !props.row.inventorySummary)"
                        v-close-popup
                        @click="openBrowseForDevice(props.row)"
                      >
                        <q-item-section avatar>
                          <q-icon name="folder_open" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("desktop.browseFolders") }}</q-item-section>
                        <q-tooltip v-if="!canBrowseDevice(props.row) && !props.row.inventorySummary">
                          {{ browseBlockedReasonFor(props.row) }}
                        </q-tooltip>
                      </q-item>
                      <q-item
                        v-if="canUpgradeDevice(props.row) || isUpgradeInProgress(props.row)"
                        clickable
                        :disable="busy || upgradingId === props.row.id || isUpgradeInProgress(props.row)"
                        v-close-popup
                        @click="upgradeAgent(props.row)"
                      >
                        <q-item-section avatar>
                          <q-icon name="system_update_alt" size="18px" />
                        </q-item-section>
                        <q-item-section>
                          {{ isUpgradeInProgress(props.row) ? t("desktop.upgrading") : t("desktop.upgradeAgent") }}
                        </q-item-section>
                      </q-item>
                      <q-separator />
                      <q-item clickable>
                        <q-item-section avatar>
                          <q-icon name="shield" size="18px" />
                        </q-item-section>
                        <q-item-section>{{ t("desktop.security") }}</q-item-section>
                        <q-item-section side>
                          <q-icon name="keyboard_arrow_right" size="18px" />
                        </q-item-section>
                        <q-menu
                          anchor="top left"
                          self="top right"
                          :offset="[8, 0]"
                          class="qka-ctrl-menu"
                        >
                          <q-list dense>
                            <q-item
                              v-close-popup
                              clickable
                              class="qka-ctrl-menu__danger"
                              @click="openRevokeConfirm(props.row)"
                            >
                              <q-item-section avatar>
                                <q-icon name="block" size="16px" />
                              </q-item-section>
                              <q-item-section>{{ t("desktop.revoke") }}</q-item-section>
                            </q-item>
                          </q-list>
                        </q-menu>
                      </q-item>
                    </q-list>
                  </q-menu>
                </q-btn>
              </div>
            </q-td>
          </template>
        </q-table>
      </q-card-section>

      <q-separator />

      <q-card-section v-if="selectedDevice">
        <div class="text-caption text-grey-7 q-mb-sm">
          {{ deviceHostname(selectedDevice) }}
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

        <div v-if="selectedDevice.readOnlyVerified" class="row items-center q-gutter-xs q-mb-md">
          <q-icon name="check_circle" color="positive" size="18px" />
          <span class="text-positive">{{ t("desktop.readOnlyVerified") }}</span>
        </div>
        <div v-else-if="isPolicyPause(selectedDevice)" class="row items-center q-gutter-xs q-mb-md">
          <q-icon name="pause_circle" color="warning" size="18px" />
          <span class="text-warning">{{ t("desktop.pausedByPolicy") }}</span>
        </div>
        <div v-else-if="isReadOnlyPermissionFailure(selectedDevice)" class="row items-center q-gutter-xs q-mb-md">
          <q-icon name="error" color="negative" size="18px" />
          <span class="text-negative">{{ t("desktop.readOnlyFailed") }}</span>
        </div>
        <div
          v-if="selectedDevice.readOnlyDetail && !isPolicyPause(selectedDevice)"
          class="text-caption text-grey-7 q-mb-sm"
        >
          {{ selectedDevice.readOnlyDetail }}
        </div>
        <div v-if="selectedDevice.lastError" class="text-caption text-negative q-mb-sm">
          {{ selectedDevice.lastError }}
        </div>

        <div class="text-caption text-grey-7 q-mb-md">
          {{ t("desktop.lastHeartbeat") }}:
          {{ selectedDevice.lastHeartbeatAt || t("desktop.never") }}
          · {{ t("desktop.agentVersion") }}: {{ formatAgentVersion(selectedDevice.agentVersion) }}
          <span v-if="selectedDevice.upgradeJob" class="q-ml-xs">
            · {{ t("desktop.upgradeStatus") }}: {{ selectedDevice.upgradeJob.status }}
            <span v-if="selectedDevice.upgradeJob.lastError" class="text-negative">
              ({{ selectedDevice.upgradeJob.lastError }})
            </span>
          </span>
        </div>

        <div class="row q-gutter-sm">
          <q-btn
            v-if="indexingUi(selectedDevice).action"
            dense
            color="primary"
            unelevated
            :loading="busy"
            :label="indexingUi(selectedDevice).action!.label"
            @click="runIndexingAction(selectedDevice)"
          />
          <q-chip
            v-if="canUpgradeDevice(selectedDevice)"
            dense
            square
            clickable
            color="orange-7"
            text-color="white"
            :disable="busy || upgradingId === selectedDevice.id"
            :label="t('desktop.upgradeAgent')"
            @click="upgradeAgent(selectedDevice)"
          >
            <q-tooltip>
              {{
                t("desktop.upgradeTooltip", {
                  from: formatAgentVersion(selectedDevice.agentVersion),
                  to: formatAgentVersion(selectedDevice.availableAgentVersion),
                })
              }}
            </q-tooltip>
          </q-chip>
        </div>

        <div class="text-caption text-grey-6 q-mt-md">{{ t("desktop.gpoNote") }}</div>
        <div class="text-caption text-grey-6">{{ selectedDevice.gpoDeployment?.note }}</div>
      </q-card-section>

      <q-card-section v-else>
        <div class="text-body2 text-grey-7">{{ t("desktop.selectDeviceManageHint") }}</div>
      </q-card-section>
    </q-card>

    <q-dialog v-model="detailsOpen">
      <q-card class="qka-details-dialog">
        <q-card-section class="text-h6">{{ t("desktop.computerDetails") }}</q-card-section>
        <q-card-section v-if="detailsLoading">
          <q-linear-progress indeterminate color="primary" />
        </q-card-section>
        <q-card-section v-else-if="detailsInventory">
          <div class="text-subtitle2">{{ detailsInventory.hostname || "—" }}</div>
          <div class="text-caption text-grey-7 q-mb-md">
            {{ detailsInventory.domainName || "—" }}
            · {{ t("desktop.lastInventorySync") }}:
            {{ detailsInventory.volumesSyncedAt || detailsInventory.hardwareSyncedAt || t("desktop.never") }}
            <span v-if="detailsInventory.hardwareStale || detailsInventory.volumesStale" class="text-warning">
              · {{ t("desktop.inventoryStale") }}
            </span>
          </div>
          <div class="q-gutter-xs q-mb-md">
            <div><span class="text-grey-7">{{ t("desktop.os") }}:</span> {{ detailsInventory.osEdition }} {{ detailsInventory.osVersion }} ({{ detailsInventory.osBuild }})</div>
            <div><span class="text-grey-7">{{ t("desktop.cpu") }}:</span> {{ detailsInventory.cpuModel }} · {{ detailsInventory.cpuPhysicalCores }}/{{ detailsInventory.cpuLogicalProcessors }}</div>
            <div><span class="text-grey-7">{{ t("desktop.ram") }}:</span> {{ formatBytes(detailsInventory.ramBytes) }}</div>
            <div><span class="text-grey-7">{{ t("desktop.hardware") }}:</span> {{ detailsInventory.manufacturer }} {{ detailsInventory.model }} · {{ detailsInventory.serialNumber || "—" }}</div>
            <div><span class="text-grey-7">{{ t("desktop.agentVersion") }}:</span> {{ detailsInventory.agentVersion || "—" }}</div>
          </div>
          <div class="text-subtitle2 q-mb-sm">{{ t("desktop.storage") }}</div>
          <div v-for="vol in detailsInventory.volumes || []" :key="vol.rootPath || vol.letter" class="q-mb-sm">
            <div class="row items-center">
              <div class="col">{{ vol.letter }} {{ vol.label ? `(${vol.label})` : "" }}</div>
              <div class="col-auto text-caption">{{ formatBytes(vol.usedBytes || 0) }} / {{ formatBytes(vol.capacityBytes || 0) }}</div>
            </div>
            <q-linear-progress
              size="8px"
              rounded
              :value="storageRatio(vol.usedBytes || 0, vol.capacityBytes || 0)"
              color="primary"
              track-color="grey-3"
            />
          </div>
        </q-card-section>
        <q-card-section v-else>
          <div class="text-body2 text-grey-7">{{ t("desktop.inventoryMissing") }}</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat :label="t('desktop.cancel')" v-close-popup />
          <q-btn
            color="primary"
            outline
            :loading="detailsBusy"
            :disable="!detailsDevice?.online"
            :label="t('desktop.refreshInventory')"
            @click="refreshInventoryForDetails"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <q-dialog v-model="revokeOpen" persistent>
      <q-card style="min-width: min(420px, 94vw)">
        <q-card-section class="text-h6">{{ t("desktop.revokeConfirmTitle") }}</q-card-section>
        <q-card-section>
          <div class="text-body2 q-mb-sm">
            <strong>{{ revokeTarget ? deviceHostname(revokeTarget) : "—" }}</strong>
          </div>
          <div class="text-body2">{{ t("desktop.revokeConfirmBody") }}</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat :label="t('desktop.cancel')" v-close-popup />
          <q-btn
            color="negative"
            unelevated
            :loading="busy"
            :label="t('desktop.revokeConfirmAction')"
            @click="confirmRevoke"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <q-dialog v-model="browseOpen" persistent>
      <q-card class="qka-browse-dialog">
        <q-card-section class="row items-center no-wrap">
          <div class="col text-h6">{{ t("desktop.browseTitle") }}</div>
          <div class="col-auto">
            <q-btn
              dense
              outline
              color="primary"
              :loading="browseBusy"
              :disable="!selectedDevice?.online"
              :label="t('desktop.refresh')"
              @click="() => refreshBrowseLive()"
            />
          </div>
        </q-card-section>
        <q-card-section>
          <div class="text-caption q-mb-sm">
            {{ selectedDevice ? deviceHostname(selectedDevice) : "—" }}
            ·
            {{
              selectedDevice?.employeeAssigned === false
                ? t("desktop.employeeUnassigned")
                : selectedDevice?.employeeName
            }}
          </div>
          <div class="text-caption text-grey-7 q-mb-sm">
            {{ t("desktop.lastInventorySync") }}: {{ browseSyncedAt || t("desktop.never") }}
            <span v-if="browseStale" class="text-warning"> · {{ t("desktop.inventoryStale") }}</span>
            <span v-if="!selectedDevice?.online" class="text-warning"> · {{ t("desktop.cachedOffline") }}</span>
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
            children-key="children"
            v-model:expanded="expandedKeys"
            @lazy-load="onLazyLoad"
          >
            <template #default-header="prop">
              <div class="row items-center no-wrap full-width">
                <q-checkbox
                  dense
                  :model-value="isSelected(prop.node.path)"
                  :disable="!prop.node.accessible || !prop.node.path || prop.node.kind === 'volume'"
                  @update:model-value="(v: boolean) => toggleSelect(prop.node, v)"
                  @click.stop
                />
                <q-icon
                  :name="prop.node.kind === 'volume' ? 'storage' : prop.node.kind === 'known_folder' ? 'folder_special' : 'folder'"
                  class="q-mx-xs"
                  size="18px"
                />
                <span>{{ prop.node.label }}</span>
                <span v-if="prop.node.capacityBytes" class="text-caption text-grey-7 q-ml-sm">
                  {{ formatBytes(prop.node.usedBytes || 0) }} / {{ formatBytes(prop.node.capacityBytes) }}
                </span>
                <span v-if="!prop.node.accessible" class="text-caption text-negative q-ml-sm">{{ t("desktop.inaccessible") }}</span>
                <span
                  v-else-if="prop.node.path && nodeLoadErrors[prop.node.path]"
                  class="text-caption text-negative q-ml-sm row items-center no-wrap"
                >
                  {{ nodeLoadErrors[prop.node.path] }}
                  <q-btn
                    flat
                    dense
                    size="sm"
                    color="primary"
                    class="q-ml-xs"
                    :label="t('desktop.retry')"
                    :disable="Boolean(childLoadInflight[prop.node.path])"
                    @click.stop="retryNodeLoad(prop.node)"
                  />
                </span>
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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { api, type DesktopDevice, type DesktopDeviceInventory, type DirectoryUser, type FolderBrowseNode } from "@/api";

type TreeNode = {
  label: string;
  path: string;
  pathKey: string;
  kind: string;
  accessible: boolean;
  hasChildren: boolean;
  detail?: string;
  capacityBytes?: number;
  freeBytes?: number;
  usedBytes?: number;
  lazy?: boolean;
  children?: TreeNode[];
};

type LazyLoadDetails = {
  node: TreeNode;
  key: string;
  done: (children?: TreeNode[]) => void;
  fail: () => void;
};

const FOLDER_NODE_KINDS = new Set(["volume", "directory", "known_folder"]);

type EmployeeFilterOption = {
  label: string;
  value: string;
  caption?: string;
  searchText: string;
};

const { t } = useI18n();
const loading = ref(false);
const busy = ref(false);
const browseBusy = ref(false);
const error = ref("");
const browseError = ref("");
const devices = ref<DesktopDevice[]>([]);
const allDevices = ref<DesktopDevice[]>([]);
const employees = ref<DirectoryUser[]>([]);
const filterUserId = ref<string | null>("");
const deviceSearch = ref("");
const filterAgentStatus = ref<string | null>(null);
const filterIndexing = ref<string | null>(null);
const assignUserId = ref<string | null>(null);
const selectedRows = ref<DesktopDevice[]>([]);
const enrollmentCode = ref("");
const enrollmentExpires = ref("");
const browseOpen = ref(false);
const detailsOpen = ref(false);
const detailsLoading = ref(false);
const detailsBusy = ref(false);
const detailsDevice = ref<DesktopDevice | null>(null);
const detailsInventory = ref<DesktopDeviceInventory | null>(null);
const treeNodes = ref<TreeNode[]>([]);
const expandedKeys = ref<string[]>([]);
const selectedFolderPaths = ref<string[]>([]);
const includeSubfolders = ref(true);
const knownMap = ref<Record<string, string>>({});
const childrenCache = ref<Record<string, TreeNode[]>>({});
/** Per-path load error messages (independent of other nodes). */
const nodeLoadErrors = ref<Record<string, string>>({});
/** Deduplicate concurrent lazy loads for the same path. */
const childLoadInflight = ref<Record<string, Promise<TreeNode[]>>>({});
const browseSyncedAt = ref("");
const browseStale = ref(false);
const filteredEmployeeOptions = ref<EmployeeFilterOption[]>([]);
const upgradingId = ref<string | null>(null);
const commandDeviceId = ref<string | null>(null);
const revokeOpen = ref(false);
const revokeTarget = ref<DesktopDevice | null>(null);
const pagination = ref({
  sortBy: "computerName",
  descending: false,
  page: 1,
  rowsPerPage: 10,
});
let pollTimer: number | undefined;

const knownShortcuts = ["Desktop", "Documents", "Downloads"];

type IndexingAction = { cmd: "start" | "pause" | "resume"; label: string };
type IndexingUi = {
  key: "not_started" | "indexing" | "up_to_date" | "paused" | "failed" | "resync_required";
  label: string;
  color: string;
  secondary: string;
  tooltip?: string;
  action: IndexingAction | null;
  sortRank: number;
};

function deviceHostname(row: DesktopDevice | null | undefined): string {
  if (!row) return "—";
  const host = (row.workstationHostname || row.computerName || "").trim();
  if (!host) return "—";
  // Never display domain NetBIOS as the computer column.
  if (host.toUpperCase() === "QEHC" || host.toUpperCase() === "WORKGROUP") return "—";
  return host;
}

function versionParts(v: string | null | undefined): number[] {
  const parts = String(v || "")
    .trim()
    .replace(/^v/i, "")
    .split(/[^\d]+/)
    .filter(Boolean)
    .map((p) => Number.parseInt(p, 10) || 0);
  while (parts.length < 3) parts.push(0);
  return parts.slice(0, 4);
}

function compareVersions(a: string | null | undefined, b: string | null | undefined): number {
  const aa = versionParts(a);
  const bb = versionParts(b);
  const n = Math.max(aa.length, bb.length);
  for (let i = 0; i < n; i += 1) {
    const d = (aa[i] || 0) - (bb[i] || 0);
    if (d !== 0) return d;
  }
  return 0;
}

function formatAgentVersion(v: string | null | undefined): string {
  const raw = String(v || "").trim();
  if (!raw) return "—";
  return raw.toLowerCase().startsWith("v") ? raw : `v${raw}`;
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return t("desktop.never");
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return t("desktop.never");
  const mins = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if (mins < 1) return t("desktop.justNow");
  if (mins < 60) return t("desktop.minutesAgo", { n: mins });
  const hours = Math.round(mins / 60);
  if (hours < 48) return t("desktop.hoursAgo", { n: hours });
  return t("desktop.daysAgo", { n: Math.round(hours / 24) });
}

function isPolicyPause(row: DesktopDevice | null | undefined): boolean {
  if (!row) return false;
  const detail = (row.readOnlyDetail || "").toLowerCase();
  if ((row.indexingState || "").toLowerCase() === "paused") return true;
  return detail.includes("paused by policy");
}

function isReadOnlyPermissionFailure(row: DesktopDevice | null | undefined): boolean {
  if (!row || row.readOnlyVerified) return false;
  if (isPolicyPause(row)) return false;
  const detail = (row.readOnlyDetail || "").trim();
  const err = (row.lastError || "").trim();
  if (!detail && !err) return false;
  // Policy / assignment messaging is not a folder ACL failure.
  const blob = `${detail} ${err}`.toLowerCase();
  if (blob.includes("paused by policy")) return false;
  if (blob.includes("unassigned")) return false;
  return true;
}

function indexingUi(row: DesktopDevice | null | undefined): IndexingUi {
  if (!row) {
    return {
      key: "not_started",
      label: t("desktop.indexingNotStarted"),
      color: "grey-7",
      secondary: t("desktop.indexingNever"),
      action: null,
      sortRank: 0,
    };
  }
  const state = (row.indexingState || "").toLowerCase();
  const indexed = Number(row.filesIndexed || 0);
  const total = Number(row.filesTotal || 0);
  const pending = Number(row.filesPending || 0);
  const failed = Number(row.filesFailed || 0);
  const pct = Number(row.percentComplete || 0);
  const canIndex = row.employeeAssigned !== false;

  if (state === "running") {
    return {
      key: "indexing",
      label: t("desktop.indexingRunning"),
      color: "primary",
      secondary: total
        ? `${indexed.toLocaleString()} / ${total.toLocaleString()} ${t("desktop.files")} (${pct}%)`
        : t("desktop.indexingScanning"),
      action: { cmd: "pause", label: t("desktop.pause") },
      sortRank: 40,
    };
  }

  if (state === "paused" || isPolicyPause(row)) {
    const when = formatRelative(row.lastIndexAt || row.lastHeartbeatAt);
    return {
      key: "paused",
      label: t("desktop.indexingPaused"),
      color: "warning",
      secondary: t("desktop.pausedAgo", { when }),
      tooltip: row.readOnlyDetail || t("desktop.pausedByPolicy"),
      action: canIndex ? { cmd: "resume", label: t("desktop.resume") } : null,
      sortRank: 30,
    };
  }

  if (state === "error" || isReadOnlyPermissionFailure(row)) {
    const detail =
      row.lastError ||
      row.readOnlyDetail ||
      t("desktop.readOnlyFailedShort");
    return {
      key: "failed",
      label: t("desktop.indexingFailed"),
      color: "negative",
      secondary: detail,
      tooltip: detail,
      action: canIndex ? { cmd: "start", label: t("desktop.retry") } : null,
      sortRank: 50,
    };
  }

  if (indexed === 0 && !row.lastIndexAt) {
    return {
      key: "not_started",
      label: t("desktop.indexingNotStarted"),
      color: "grey-7",
      secondary: t("desktop.indexingNever"),
      action: canIndex ? { cmd: "start", label: t("desktop.start") } : null,
      sortRank: 0,
    };
  }

  if (pending > 0 || (total > 0 && indexed < total && failed > 0)) {
    return {
      key: "resync_required",
      label: t("desktop.indexingResyncRequired"),
      color: "orange",
      secondary:
        pending > 0
          ? t("desktop.indexingPendingFiles", { n: pending })
          : `${indexed.toLocaleString()} / ${total.toLocaleString()} · ${t("desktop.indexingIncomplete")}`,
      action: canIndex ? { cmd: "start", label: t("desktop.resync") } : null,
      sortRank: 20,
    };
  }

  return {
    key: "up_to_date",
    label: t("desktop.indexingUpToDate"),
    color: "positive",
    secondary: t("desktop.lastSyncedAgo", { when: formatRelative(row.lastIndexAt || row.lastHeartbeatAt) }),
    action: canIndex ? { cmd: "start", label: t("desktop.resync") } : null,
    sortRank: 10,
  };
}

const agentStatusFilterOptions = [
  { label: t("desktop.filterAgentOnline"), value: "online", icon: "wifi" },
  { label: t("desktop.filterAgentOffline"), value: "offline", icon: "wifi_off" },
];

const indexingFilterOptions = [
  { label: t("desktop.indexingRunning"), value: "indexing", icon: "sync" },
  { label: t("desktop.indexingPaused"), value: "paused", icon: "pause_circle" },
  { label: t("desktop.indexingUpToDate"), value: "up_to_date", icon: "check_circle" },
  { label: t("desktop.indexingFailed"), value: "failed", icon: "error" },
  { label: t("desktop.indexingNotStarted"), value: "not_started", icon: "hourglass_empty" },
  { label: t("desktop.indexingResyncRequired"), value: "resync_required", icon: "sync_problem" },
];

const agentStatusFilterDisplay = computed(() => {
  const match = agentStatusFilterOptions.find((o) => o.value === filterAgentStatus.value);
  return match?.label || t("desktop.filterAgentAll");
});

const indexingFilterDisplay = computed(() => {
  const match = indexingFilterOptions.find((o) => o.value === filterIndexing.value);
  return match?.label || t("desktop.filterIndexingAll");
});

function onFilterInputFocus(evt: Event) {
  const el = evt.target;
  if (!(el instanceof HTMLInputElement)) return;
  // fill-input selects all text on focus; collapse so opening the menu does not flash a highlight.
  const len = el.value.length;
  requestAnimationFrame(() => {
    try {
      el.setSelectionRange(len, len);
    } catch {
      /* ignore unsupported selection ranges */
    }
  });
}

const columns = [
  {
    name: "computerName",
    label: t("desktop.computer"),
    field: (row: DesktopDevice) => deviceHostname(row),
    align: "left" as const,
    sortable: true,
  },
  {
    name: "employeeName",
    label: t("desktop.employee"),
    field: "employeeName",
    align: "left" as const,
    sortable: true,
  },
  {
    name: "agentStatus",
    label: t("desktop.agent"),
    field: (row: DesktopDevice) => (row.online ? "connected" : "offline"),
    align: "left" as const,
    sortable: true,
  },
  {
    name: "agentVersion",
    label: t("desktop.agentVersion"),
    field: (row: DesktopDevice) => row.agentVersion || "",
    align: "left" as const,
    sortable: true,
    sort: (a: string, b: string) => compareVersions(a, b),
  },
  {
    name: "indexed",
    label: t("desktop.indexed"),
    field: "filesIndexed",
    align: "left" as const,
    sortable: true,
    sort: (a: number, b: number) => Number(a || 0) - Number(b || 0),
  },
  {
    name: "indexing",
    label: t("desktop.indexingColumn"),
    field: (row: DesktopDevice) => indexingUi(row).sortRank,
    align: "left" as const,
    sortable: true,
    sort: (a: number, b: number) => Number(a || 0) - Number(b || 0),
  },
  {
    name: "status",
    label: t("desktop.status"),
    field: "status",
    align: "left" as const,
    sortable: true,
  },
  {
    name: "actions",
    label: t("desktop.actions"),
    field: "id",
    align: "right" as const,
    sortable: false,
    required: true,
  },
];

function indexingChipClass(row: DesktopDevice | null | undefined): string {
  const key = indexingUi(row).key;
  if (key === "indexing") return "qka-ctrl-chip--indexing";
  if (key === "paused") return "qka-ctrl-chip--paused";
  if (key === "failed") return "qka-ctrl-chip--failed";
  if (key === "up_to_date") return "qka-ctrl-chip--online";
  if (key === "resync_required") return "qka-ctrl-chip--paused";
  return "qka-ctrl-chip--neutral";
}

const employeeOptions = computed(() =>
  employees.value
    .filter((e) => e.qkaUserId && (e.linkedIdentities || "").includes("ad"))
    .map((e) => {
      const email = (e.primaryEmail || e.contactEmail || "").trim();
      return {
        label: e.displayName || email || "Employee",
        value: e.qkaUserId as string,
        caption: email && email !== e.displayName ? email : undefined,
      };
    }),
);

const employeeFilterOptions = computed((): EmployeeFilterOption[] => {
  const byUser = new Map<string, EmployeeFilterOption>();
  for (const d of allDevices.value) {
    if (!d.qkaUserId || d.employeeAssigned === false) continue;
    const host = deviceHostname(d);
    const email = (d.employeeEmail || "").trim();
    const name = (d.employeeName || "").trim() || email || "Employee";
    const existing = byUser.get(d.qkaUserId);
    if (existing) {
      if (host !== "—" && !existing.searchText.includes(host.toLowerCase())) {
        existing.searchText += ` ${host.toLowerCase()}`;
      }
      continue;
    }
    byUser.set(d.qkaUserId, {
      label: name,
      value: d.qkaUserId,
      caption: email && email !== name ? email : undefined,
      searchText: `${name} ${email} ${host}`.toLowerCase(),
    });
  }
  const employeesOnly = [...byUser.values()].sort((a, b) => a.label.localeCompare(b.label));
  return [
    { label: t("desktop.filterAllEmployees"), value: "", searchText: "all employees" },
    { label: t("desktop.filterUnassigned"), value: "__unassigned__", searchText: "unassigned" },
    ...employeesOnly,
  ];
});

const selectedDevice = computed(() => selectedRows.value[0] || null);

function canBrowseDevice(row: DesktopDevice | null | undefined): boolean {
  return Boolean(row?.folderBrowseAvailable && row?.online);
}

function browseBlockedReasonFor(row: DesktopDevice | null | undefined): string {
  if (!row) return t("desktop.selectDeviceManageHint");
  if (!row.online || row.folderBrowseAvailable === false) {
    return row.folderBrowseBlockedReason || t("desktop.browseOffline");
  }
  return row.folderBrowseBlockedReason || t("desktop.browseOffline");
}

function isUpgradeInProgress(row: DesktopDevice | null | undefined): boolean {
  const st = (row?.upgradeJob?.status || "").toLowerCase();
  return st === "scheduled" || st === "upgrading" || st === "verifying";
}

function canUpgradeDevice(row: DesktopDevice | null | undefined): boolean {
  if (!row) return false;
  if (isUpgradeInProgress(row)) return false;
  // Prefer server flag; fall back to numeric compare so chips stay correct if DTO omits it.
  if (typeof row.upgradeAvailable === "boolean") return row.upgradeAvailable;
  const avail = (row.availableAgentVersion || "").trim();
  if (!avail) return false;
  return compareVersions(row.agentVersion, avail) < 0;
}

async function upgradeAgent(row: DesktopDevice | null | undefined) {
  if (!row || !canUpgradeDevice(row)) return;
  busy.value = true;
  upgradingId.value = row.id;
  error.value = "";
  try {
    await api.upgradeDesktopDevice(row.id);
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.upgradeError");
  } finally {
    upgradingId.value = null;
    busy.value = false;
  }
}

const indexBadgeColor = computed(() => indexingUi(selectedDevice.value).color);

const indexBadgeLabel = computed(() => indexingUi(selectedDevice.value).label);

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

function formatBytes(n: number): string {
  let val = Math.max(0, Number(n) || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  for (const unit of units) {
    if (val < 1024 || unit === "TB") {
      return unit === "B" ? `${Math.round(val)} ${unit}` : `${val.toFixed(1)} ${unit}`;
    }
    val /= 1024;
  }
  return `${n} B`;
}

function storageRatio(used: number, capacity: number): number {
  if (!capacity) return 0;
  return Math.min(1, Math.max(0, used / capacity));
}

function isFolderBrowseNode(n: FolderBrowseNode): boolean {
  return FOLDER_NODE_KINDS.has(String(n.kind || ""));
}

function toTreeNode(n: FolderBrowseNode): TreeNode {
  const path = n.path || `virtual:${n.name}`;
  const cached = path && childrenCache.value[path] ? childrenCache.value[path] : undefined;
  const canLazy = Boolean(n.hasChildren && n.path && n.accessible);
  return {
    label: n.name,
    path: n.path || "",
    pathKey: path,
    kind: n.kind,
    accessible: n.accessible,
    hasChildren: n.hasChildren,
    detail: n.detail,
    capacityBytes: n.capacityBytes,
    freeBytes: n.freeBytes,
    usedBytes: n.usedBytes,
    // Quasar lazy: leave children undefined until @lazy-load calls done().
    // Pre-seed from cache so re-opened roots keep expanded children without refetch.
    lazy: canLazy && !cached,
    children: cached ? cached.map(attachChildren) : undefined,
  };
}

function denialPlaceholder(parentPath: string, detail: string): TreeNode {
  return {
    label: t("desktop.inaccessible"),
    path: "",
    pathKey: `${parentPath}::denied`,
    kind: "directory",
    accessible: false,
    hasChildren: false,
    detail,
    lazy: false,
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

function applyRootNodes(nodes: FolderBrowseNode[]) {
  knownMap.value = {};
  for (const n of nodes) {
    if ((n.kind === "known_folder" || n.name === "Desktop" || n.name === "Documents" || n.name === "Downloads") && n.accessible && n.path) {
      knownMap.value[n.name] = n.path;
    }
  }
  const prevExpanded = [...expandedKeys.value];
  treeNodes.value = nodes.filter(isFolderBrowseNode).map(toTreeNode);
  // Keep previously expanded keys that still exist after a roots refresh.
  const keys = new Set(treeNodes.value.map((n) => n.pathKey));
  const walk = (list: TreeNode[]) => {
    for (const n of list) {
      keys.add(n.pathKey);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(treeNodes.value);
  expandedKeys.value = prevExpanded.filter((k) => keys.has(k));
}

async function loadRoots() {
  if (!selectedDevice.value) return;
  browseBusy.value = true;
  browseError.value = "";
  try {
    // Cached roots first (instant), then live refresh when online.
    const roots = await api.desktopBrowseRoots(selectedDevice.value.id);
    browseSyncedAt.value = roots.syncedAt || "";
    browseStale.value = Boolean(roots.stale);
    applyRootNodes(roots.nodes || []);
    if (selectedDevice.value.online && canBrowseDevice(selectedDevice.value)) {
      await refreshBrowseLive(false);
    } else if (!roots.nodes?.length) {
      browseError.value = t("desktop.cachedOffline");
    }
  } catch (err) {
    browseError.value = err instanceof Error ? err.message : t("desktop.browseError");
    treeNodes.value = [];
  } finally {
    browseBusy.value = false;
  }
}

async function refreshBrowseLive(showBusy = true) {
  if (!selectedDevice.value?.online) return;
  if (showBusy) browseBusy.value = true;
  browseError.value = "";
  try {
    await api.refreshDesktopDeviceInventory(selectedDevice.value.id);
    const req = await api.requestDesktopFolderBrowse(selectedDevice.value.id, "");
    const result = await waitBrowse(selectedDevice.value.id, req.id);
    if (result.error) browseError.value = result.error;
    applyRootNodes(result.nodes || []);
    browseSyncedAt.value = result.completedAt || new Date().toISOString();
    browseStale.value = false;
  } catch (err) {
    browseError.value = err instanceof Error ? err.message : t("desktop.browseError");
  } finally {
    if (showBusy) browseBusy.value = false;
  }
}

/**
 * Fetch immediate child directories for one parent. Empty completed results return [].
 * Access-denied becomes a single inaccessible placeholder (not a thrown error).
 * Transport/timeout failures throw so Quasar can clear its spinner via fail()+Retry.
 */
async function fetchChildNodes(parentPath: string): Promise<TreeNode[]> {
  const device = selectedDevice.value;
  if (!device) throw new Error(t("desktop.browseError"));

  if (childrenCache.value[parentPath]) {
    return childrenCache.value[parentPath];
  }

  // Prefer server folder cache when fresh (or when offline).
  try {
    const cached = await api.desktopFolderCache(device.id, parentPath);
    if (cached.cache && !cached.cache.stale) {
      const kids = (cached.cache.nodes || []).filter(isFolderBrowseNode).map(toTreeNode);
      // Cached empty folder is success — do not treat as miss.
      childrenCache.value = { ...childrenCache.value, [parentPath]: kids };
      return kids;
    }
    if (!device.online) {
      if (cached.cache?.nodes) {
        const kids = cached.cache.nodes.filter(isFolderBrowseNode).map(toTreeNode);
        childrenCache.value = { ...childrenCache.value, [parentPath]: kids };
        return kids;
      }
      throw new Error(t("desktop.cachedOffline"));
    }
  } catch (err) {
    if (!device.online) {
      throw err instanceof Error ? err : new Error(t("desktop.cachedOffline"));
    }
  }

  if (!device.online) {
    throw new Error(t("desktop.cachedOffline"));
  }

  const req = await api.requestDesktopFolderBrowse(device.id, parentPath);
  const result = await waitBrowse(device.id, req.id);
  let kids = (result.nodes || []).filter(isFolderBrowseNode).map(toTreeNode);
  const errText = (result.error || "").toLowerCase();
  const accessDenied = errText.includes("access denied");

  if (result.status === "failed" || result.status === "expired") {
    if (accessDenied || !kids.length) {
      kids = [denialPlaceholder(parentPath, result.error || t("desktop.inaccessible"))];
    }
  } else if (!kids.length && accessDenied) {
    kids = [denialPlaceholder(parentPath, result.error || t("desktop.inaccessible"))];
  }
  // Completed + empty + no access-denied ⇒ genuinely empty folder ([]).

  childrenCache.value = { ...childrenCache.value, [parentPath]: kids };
  return kids;
}

function attachChildren(node: TreeNode): TreeNode {
  if (!node.path) return node;
  const kids = childrenCache.value[node.path];
  if (!kids) {
    return {
      ...node,
      lazy: Boolean(node.hasChildren && node.accessible && node.path),
      children: undefined,
    };
  }
  return {
    ...node,
    lazy: false,
    children: kids.map(attachChildren),
  };
}

async function onLazyLoad(details: LazyLoadDetails) {
  const { node, done, fail } = details;
  const parentPath = node.path || "";
  if (!parentPath || parentPath.startsWith("virtual:")) {
    done([]);
    return;
  }
  if (!node.accessible) {
    done([denialPlaceholder(parentPath, node.detail || t("desktop.inaccessible"))]);
    return;
  }

  const existing = childrenCache.value[parentPath];
  if (existing) {
    delete nodeLoadErrors.value[parentPath];
    done(existing.map(attachChildren));
    return;
  }

  let pending = childLoadInflight.value[parentPath];
  if (!pending) {
    pending = fetchChildNodes(parentPath).finally(() => {
      const next = { ...childLoadInflight.value };
      delete next[parentPath];
      childLoadInflight.value = next;
    });
    childLoadInflight.value = { ...childLoadInflight.value, [parentPath]: pending };
  }

  try {
    const kids = await pending;
    const nextErr = { ...nodeLoadErrors.value };
    delete nextErr[parentPath];
    nodeLoadErrors.value = nextErr;
    done(kids.map(attachChildren));
  } catch (err) {
    const message = err instanceof Error ? err.message : t("desktop.browseNodeFailed");
    nodeLoadErrors.value = { ...nodeLoadErrors.value, [parentPath]: message };
    // Clears Quasar's per-node spinner; node stays collapsed until Retry re-expands.
    fail();
  }
}

async function retryNodeLoad(node: TreeNode) {
  const parentPath = node.path;
  if (!parentPath || !selectedDevice.value) return;
  const nextErr = { ...nodeLoadErrors.value };
  delete nextErr[parentPath];
  nodeLoadErrors.value = nextErr;
  const nextCache = { ...childrenCache.value };
  delete nextCache[parentPath];
  childrenCache.value = nextCache;
  node.children = undefined;
  node.lazy = true;
  node.hasChildren = true;

  // Drop then re-add expanded key so Quasar re-enters lazy-load.
  expandedKeys.value = expandedKeys.value.filter((k) => k !== node.pathKey);
  await nextTick();
  if (!expandedKeys.value.includes(node.pathKey)) {
    expandedKeys.value = [...expandedKeys.value, node.pathKey];
  }
}

function openBrowseForDevice(row: DesktopDevice) {
  // Online live browse, or offline cached inventory/roots.
  if (!canBrowseDevice(row) && !row.inventorySummary) return;
  selectedRows.value = [row];
  assignUserId.value = row.qkaUserId || null;
  selectedFolderPaths.value = (row.folders || []).map((f) => f.path);
  includeSubfolders.value = true;
  childrenCache.value = {};
  nodeLoadErrors.value = {};
  childLoadInflight.value = {};
  expandedKeys.value = [];
  treeNodes.value = [];
  browseSyncedAt.value = row.inventorySummary?.volumesSyncedAt || "";
  browseStale.value = Boolean(row.inventorySummary?.volumesStale);
  browseOpen.value = true;
  void loadRoots();
}

async function openComputerDetails(row: DesktopDevice) {
  detailsDevice.value = row;
  selectedRows.value = [row];
  detailsOpen.value = true;
  detailsLoading.value = true;
  detailsInventory.value = null;
  try {
    const res = await api.desktopDeviceInventory(row.id);
    detailsInventory.value = res.inventory;
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.loadError");
  } finally {
    detailsLoading.value = false;
  }
}

async function refreshInventoryForDetails() {
  if (!detailsDevice.value?.online) return;
  detailsBusy.value = true;
  try {
    await api.refreshDesktopDeviceInventory(detailsDevice.value.id);
    // Give the agent a moment to observe the policy flag on next poll.
    await new Promise((r) => setTimeout(r, 12000));
    const res = await api.desktopDeviceInventory(detailsDevice.value.id);
    detailsInventory.value = res.inventory;
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.loadError");
  } finally {
    detailsBusy.value = false;
  }
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

function applyDeviceFilter(items: DesktopDevice[]): DesktopDevice[] {
  let next = items;
  const filter = filterUserId.value;
  if (filter && filter !== "") {
    if (filter === "__unassigned__") {
      next = next.filter((d) => d.employeeAssigned === false || !d.qkaUserId);
    } else {
      next = next.filter((d) => d.qkaUserId === filter);
    }
  }

  const agent = filterAgentStatus.value;
  if (agent === "online") next = next.filter((d) => d.online);
  if (agent === "offline") next = next.filter((d) => !d.online);

  const indexing = filterIndexing.value;
  if (indexing) next = next.filter((d) => indexingUi(d).key === indexing);

  const needle = deviceSearch.value.trim().toLowerCase();
  if (needle) {
    next = next.filter((d) => {
      const blob = [
        deviceHostname(d),
        d.employeeName || "",
        d.employeeEmail || "",
        d.computerName || "",
        d.workstationHostname || "",
        d.agentVersion || "",
      ]
        .join(" ")
        .toLowerCase();
      return blob.includes(needle);
    });
  }

  return next;
}

function onEmployeeFilter(val: string, update: (fn: () => void) => void) {
  update(() => {
    const needle = (val || "").trim().toLowerCase();
    const all = employeeFilterOptions.value;
    filteredEmployeeOptions.value = needle
      ? all.filter((opt) => opt.searchText.includes(needle) || opt.label.toLowerCase().includes(needle))
      : all;
  });
}

async function reload() {
  loading.value = true;
  error.value = "";
  try {
    const selectedId = selectedDevice.value?.id;
    const [devicePage, userPage] = await Promise.all([
      api.desktopDevices(),
      api.listDirectoryUsers({ pageSize: 200 }),
    ]);
    allDevices.value = devicePage.items;
    employees.value = userPage.items;
    devices.value = applyDeviceFilter(devicePage.items);
    filteredEmployeeOptions.value = employeeFilterOptions.value;
    if (selectedId) {
      const keep = devices.value.find((d) => d.id === selectedId);
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
  // Keep selection if the selected row remains visible after filtering.
  const selectedId = selectedDevice.value?.id;
  devices.value = applyDeviceFilter(allDevices.value);
  if (selectedId) {
    const keep = devices.value.find((d) => d.id === selectedId);
    selectedRows.value = keep ? [keep] : [];
  } else {
    selectedRows.value = [];
  }
  pagination.value = { ...pagination.value, page: 1 };
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

async function command(cmd: "start" | "pause" | "resume", device?: DesktopDevice | null) {
  const target = device || selectedDevice.value;
  if (!target) return;
  busy.value = true;
  commandDeviceId.value = target.id;
  error.value = "";
  try {
    await api.desktopDeviceCommand(target.id, cmd);
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("desktop.commandError");
  } finally {
    commandDeviceId.value = null;
    busy.value = false;
  }
}

async function runIndexingAction(row: DesktopDevice | null | undefined) {
  const action = indexingUi(row).action;
  if (!row || !action) return;
  await command(action.cmd, row);
}

function openRevokeConfirm(row: DesktopDevice) {
  revokeTarget.value = row;
  revokeOpen.value = true;
}

async function confirmRevoke() {
  if (!revokeTarget.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api.revokeDesktopDevice(revokeTarget.value.id);
    if (selectedDevice.value?.id === revokeTarget.value.id) selectedRows.value = [];
    revokeOpen.value = false;
    revokeTarget.value = null;
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

watch(employeeFilterOptions, (opts) => {
  filteredEmployeeOptions.value = opts;
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

<style scoped>
.qka-agent-actions-cell {
  min-width: 48px;
  width: 56px;
  white-space: nowrap;
}

.qka-indexing-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 2px;
  min-width: 140px;
  max-width: 240px;
}

.qka-indexing-secondary {
  line-height: 1.25;
  margin-top: 0;
  white-space: normal;
}

.qka-browse-dialog {
  min-width: min(560px, 94vw);
  max-width: 760px;
  width: 94vw;
}

.qka-details-dialog {
  min-width: min(480px, 94vw);
  max-width: 640px;
  width: 94vw;
}

.qka-agent-devices-table :deep(th:last-child),
.qka-agent-devices-table :deep(td:last-child) {
  position: sticky;
  right: 0;
  z-index: 2;
  background: var(--q-card-bg, #fff);
  box-shadow: -4px 0 6px -6px rgba(16, 32, 51, 0.12);
}

.body--dark .qka-agent-devices-table :deep(th:last-child),
.body--dark .qka-agent-devices-table :deep(td:last-child) {
  background: var(--q-dark-page, #1d1d1d);
  box-shadow: -4px 0 6px -6px rgba(0, 0, 0, 0.35);
}

@media (max-width: 599px) {
  .qka-agent-devices-table :deep(th),
  .qka-agent-devices-table :deep(td) {
    padding-left: 6px;
    padding-right: 6px;
  }
}
</style>
