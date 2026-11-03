<template>
  <q-page class="qka-search">
    <div class="q-pa-md q-pb-sm">
      <h1 class="text-h5 q-mb-sm">{{ t("search.title") }}</h1>

      <div v-if="catalog.sources.length" class="qka-search-catalog q-mb-md">
        <div v-for="source in catalog.sources" :key="source.approvedShareId" class="qka-search-source">
          <div class="row items-center no-wrap q-gutter-sm q-mb-sm">
            <q-chip outline dense icon="folder" color="primary" text-color="primary">
              {{ source.shareName }}
            </q-chip>
            <q-badge outline color="primary">{{ source.totalFiles }} {{ t("search.files") }}</q-badge>
          </div>
          <div class="row items-center q-gutter-sm">
            <q-chip
              v-for="ft in source.types"
              :key="ft.key"
              clickable
              dense
              :outline="selectedType !== ft.key"
              :color="selectedType === ft.key ? 'primary' : undefined"
              :text-color="selectedType === ft.key ? 'white' : undefined"
              :class="[`qka-type-${ft.key}`, selectedType === ft.key ? 'qka-type-active' : '']"
              @click="selectType(ft.key)"
            >
              <q-icon :name="ft.icon" size="18px" class="q-mr-xs" />
              <span>{{ ft.label }}</span>
              <q-badge rounded :color="selectedType === ft.key ? 'white' : badgeColor(ft.key)" :text-color="selectedType === ft.key ? 'primary' : undefined" class="q-ml-sm">
                {{ ft.count }}
              </q-badge>
            </q-chip>
          </div>
        </div>
      </div>
      <div v-else-if="catalogLoaded && !catalog.error" class="text-caption text-grey-7 q-mb-sm">
        {{ t("search.noSources") }}
      </div>

      <div v-if="selectedType" class="row items-center q-gutter-sm q-mb-sm">
        <q-chip color="primary" text-color="white" dense>
          {{ selectedTypeLabel }} {{ t("search.selected") }}
        </q-chip>
        <q-btn dense outline color="primary" :label="t('search.clearFilter')" @click="clearFilter" />
      </div>

      <div class="row q-col-gutter-sm items-center">
        <div class="col">
          <q-input
            v-model="query"
            outlined
            dense
            clearable
            :label="t('search.query')"
            @keyup.enter="run(1)"
            @clear="onClearQuery"
          />
        </div>
        <div class="col-auto">
          <q-btn color="primary" unelevated :label="t('search.run')" :loading="loading" :disable="!session.user" @click="run(1)" />
        </div>
      </div>
      <div class="text-caption text-grey-7 q-mt-xs">
        <span v-if="selectedType">{{ t("search.scopedHint", { type: selectedTypeLabel }) }}</span>
        <span v-else>{{ t("search.allTypesHint") }}</span>
        <span v-if="ran"> · {{ total }} {{ t("search.results") }}</span>
      </div>
      <q-banner v-if="catalog.error" class="bg-negative text-white q-mt-sm">{{ catalog.error }}</q-banner>
      <q-banner v-if="error" class="bg-negative text-white q-mt-sm">{{ error }}</q-banner>
      <q-banner v-if="openInfo" dense class="bg-primary text-white q-mt-sm">{{ openInfo }}</q-banner>
      <q-banner v-if="openError" dense class="bg-negative text-white q-mt-sm">{{ openError }}</q-banner>
    </div>

    <div v-if="$q.screen.lt.md && selectedId" class="col qka-mobile-preview">
      <DocumentPreviewPane
        :document-id="selectedId"
        :hit-title="selectedHit?.title"
        :hit-file-type="selectedHit?.fileType"
        @close="closePreview"
      />
    </div>
    <q-splitter
      v-else
      v-model="splitter"
      :limits="[28, 72]"
      class="col qka-split"
      @update:model-value="persistSplitter"
    >
      <template #before>
        <div class="qka-results column">
          <q-linear-progress v-if="loading" indeterminate color="primary" />
          <q-table
            v-if="results.length"
            flat
            dense
            class="col qka-table"
            :rows="results"
            :columns="columns"
            row-key="id"
            :pagination="{ rowsPerPage: 0 }"
            hide-pagination
            :rows-per-page-options="[0]"
            virtual-scroll
            :virtual-scroll-sticky-size-start="28"
            style="height: 100%"
            :row-class="(row) => (row.id === selectedId ? 'bg-blue-1' : '')"
            @row-click="onRowClick"
          >
            <template #body-cell-file="props">
              <q-td :props="props">
                <div class="row items-center no-wrap">
                  <q-icon :name="iconFor(props.row.fileType)" :class="`qka-type-${props.row.fileType}`" class="q-mr-sm" />
                  <div class="ellipsis">
                    <div class="ellipsis">{{ props.row.title }}</div>
                    <div class="text-caption text-grey-7 ellipsis">{{ props.row.folder || props.row.relativePath }}</div>
                  </div>
                </div>
              </q-td>
            </template>
            <template #body-cell-preview="props">
              <q-td :props="props">
                <q-badge outline :color="previewColor(props.row.previewAvailability)">
                  {{ previewLabel(props.row.previewAvailability) }}
                </q-badge>
              </q-td>
            </template>
            <template #body-cell-actions="props">
              <q-td :props="props" class="qka-actions-cell" @click.stop>
                <q-btn
                  v-if="props.row.canDownload"
                  flat
                  dense
                  round
                  size="sm"
                  icon="open_in_new"
                  :aria-label="openOriginalTooltip(props.row)"
                  :loading="openingId === props.row.id"
                  :disable="Boolean(openingId)"
                  class="qka-open-original"
                  @click.stop.prevent="openOriginal(props.row)"
                  @mousedown.stop
                  @keydown.enter.stop.prevent="openOriginal(props.row)"
                  @keydown.space.stop.prevent="openOriginal(props.row)"
                >
                  <q-tooltip>{{ openOriginalTooltip(props.row) }}</q-tooltip>
                </q-btn>
              </q-td>
            </template>
          </q-table>
          <div v-else-if="loading" class="text-body2 q-pa-md">{{ t("search.loading") }}</div>
          <div v-else-if="ran" class="text-body2 q-pa-md">{{ emptyMessage }}</div>
          <div v-if="total > pageSize" class="row items-center justify-between q-pa-sm">
            <q-btn dense flat :disable="page <= 1 || loading" :label="t('search.prevPage')" @click="run(page - 1)" />
            <div class="text-caption">{{ page }} / {{ totalPages }}</div>
            <q-btn dense flat :disable="page >= totalPages || loading" :label="t('search.nextPage')" @click="run(page + 1)" />
          </div>
        </div>
      </template>
      <template #after>
        <DocumentPreviewPane
          :document-id="selectedId"
          :hit-title="selectedHit?.title"
          :hit-file-type="selectedHit?.fileType"
          @close="closePreview"
        />
      </template>
    </q-splitter>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useQuasar, type QTableColumn } from "quasar";
import { useI18n } from "vue-i18n";
import DocumentPreviewPane from "@/components/DocumentPreviewPane.vue";
import { api } from "@/api";
import { useSessionStore } from "@/stores/session";

type CatalogSource = {
  approvedShareId: string;
  shareName: string;
  totalFiles: number;
  types: Array<{ key: string; label: string; icon: string; count: number }>;
};
type SearchHit = {
  id: string;
  title: string;
  snippet: string;
  fileType?: string;
  relativePath?: string;
  folder?: string;
  extension?: string;
  sizeBytes?: number;
  modifiedAt?: string | null;
  matchType?: string;
  previewAvailability?: string;
  source?: string;
  canDownload?: boolean;
  openWithLabel?: string | null;
  recommendedApp?: string | null;
};

const SPLITTER_KEY = "qka.search.splitter";
const { t } = useI18n();
const $q = useQuasar();
const session = useSessionStore();
const query = ref("");
const results = ref<SearchHit[]>([]);
const error = ref("");
const ran = ref(false);
const loading = ref(false);
const selectedId = ref<string | null>(null);
const selectedType = ref<string | null>(null);
const page = ref(1);
const pageSize = ref(50);
const total = ref(0);
const splitter = ref(Number(localStorage.getItem(SPLITTER_KEY) || 40));
const catalogLoaded = ref(false);
const catalog = ref<{ sources: CatalogSource[]; totalFiles: number; error: string | null }>({
  sources: [],
  totalFiles: 0,
  error: null,
});
const openingId = ref<string | null>(null);
const openInfo = ref("");
const openError = ref("");
let abort: AbortController | null = null;

const selectedHit = computed(() => results.value.find((item) => item.id === selectedId.value));
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)));
const selectedTypeLabel = computed(() => {
  for (const source of catalog.value.sources) {
    const hit = source.types.find((item) => item.key === selectedType.value);
    if (hit) return hit.label;
  }
  return selectedType.value || "";
});
const emptyMessage = computed(() => {
  if (selectedType.value && query.value.trim()) return t("search.noneScopedQuery", { type: selectedTypeLabel.value });
  if (selectedType.value) return t("search.noneScoped", { type: selectedTypeLabel.value });
  if (query.value.trim()) return t("search.noneQuery");
  return t("search.none");
});

const columns = computed<QTableColumn[]>(() => [
  { name: "file", label: t("search.colFile"), field: "title", align: "left", style: "width: 45%" },
  { name: "type", label: t("search.colType"), field: "fileType", align: "left" },
  {
    name: "size",
    label: t("search.colSize"),
    field: "sizeBytes",
    align: "right",
    format: (val: number) => formatSize(val),
  },
  {
    name: "modified",
    label: t("search.colModified"),
    field: "modifiedAt",
    align: "left",
    format: (val: string | null | undefined) => formatDate(val),
  },
  { name: "preview", label: t("search.colPreview"), field: "previewAvailability", align: "left" },
  {
    name: "actions",
    label: "",
    field: "id",
    align: "right",
    style: "width: 48px",
    headerStyle: "width: 48px",
  },
]);

function persistSplitter(value: number) {
  localStorage.setItem(SPLITTER_KEY, String(value));
}

function iconFor(type: string | undefined) {
  if (type === "excel" || type === "csv") return "table_chart";
  if (type === "pdf") return "picture_as_pdf";
  if (type === "word") return "description";
  if (type === "powerpoint") return "slideshow";
  if (type === "images") return "image";
  if (type === "text") return "article";
  return "insert_drive_file";
}

function badgeColor(key: string) {
  if (key === "excel") return "positive";
  if (key === "word") return "primary";
  if (key === "pdf") return "negative";
  if (key === "powerpoint") return "orange";
  if (key === "csv") return "teal";
  return "grey-7";
}

function formatSize(bytes: number | undefined) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 102.4) / 10} KB`;
  return `${Math.round(value / 104857.6) / 10} MB`;
}

function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function previewLabel(value: string | undefined) {
  if (value === "indexed") return t("search.previewIndexedShort");
  if (value === "derivable") return t("search.previewDerivable");
  return t("search.previewUnavailableShort");
}

function previewColor(value: string | undefined) {
  if (value === "indexed") return "positive";
  if (value === "derivable") return "primary";
  return "grey-6";
}

function onRowClick(_evt: Event, row: SearchHit) {
  selectedId.value = row.id;
  openInfo.value = "";
  openError.value = "";
}

function closePreview() {
  selectedId.value = null;
}

function openOriginalTooltip(row: SearchHit) {
  const ft = (row.fileType || "").toLowerCase();
  const ext = (row.extension || "").toLowerCase().replace(/^\./, "");
  if (ft === "excel" || ["xlsx", "xls", "xlsm", "xlsb"].includes(ext)) return t("search.openOriginalExcel");
  if (ft === "word" || ["docx", "doc"].includes(ext)) return t("search.openOriginalWord");
  if (ft === "powerpoint" || ["pptx", "ppt"].includes(ext)) return t("search.openOriginalPowerPoint");
  if (ft === "pdf" || ext === "pdf") return t("search.openOriginalPdf");
  if (ft === "csv" || ext === "csv") return t("search.openOriginalCsv");
  if (ft === "text" || ["txt", "md", "log"].includes(ext)) return t("search.openOriginalText");
  if (ft === "images" || ["png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"].includes(ext)) {
    return t("search.openOriginalImage");
  }
  return t("search.openOriginalGeneric");
}

function saveBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function openOriginal(row: SearchHit) {
  if (!row.canDownload || openingId.value) return;
  const previousSelected = selectedId.value;
  openingId.value = row.id;
  openInfo.value = t("search.openOriginalPreparing");
  openError.value = "";
  try {
    const body = await api.documentDownload(row.id, { intent: "open" });
    if (!body.ok) {
      openInfo.value = "";
      openError.value = body.error || t("search.downloadDenied");
      return;
    }
    saveBlob(body.blob, body.fileName);
    const app = body.recommendedApp || row.recommendedApp || t("search.systemDefaultApp");
    openInfo.value = t("search.openWithInstructions", { app, file: body.fileName });
  } catch (err) {
    openInfo.value = "";
    openError.value = err instanceof Error ? err.message : t("search.downloadDenied");
  } finally {
    openingId.value = null;
    // Open Original must not change preview selection.
    selectedId.value = previousSelected;
  }
}

function selectType(key: string) {
  selectedType.value = key;
  void run(1);
}

function clearFilter() {
  selectedType.value = null;
  void run(1);
}

function onClearQuery() {
  query.value = "";
  void run(1);
}

async function loadCatalog() {
  if (!session.user) {
    catalogLoaded.value = true;
    return;
  }
  try {
    catalog.value = await api.searchCatalog();
  } catch (err) {
    catalog.value = {
      sources: [],
      totalFiles: 0,
      error: err instanceof Error ? err.message : t("search.catalogError"),
    };
  } finally {
    catalogLoaded.value = true;
  }
}

async function run(nextPage = 1) {
  if (!session.user) return;
  abort?.abort();
  abort = new AbortController();
  const current = abort;
  loading.value = true;
  ran.value = true;
  page.value = nextPage;
  error.value = "";
  try {
    const body = await api.search({
      q: query.value.trim() || undefined,
      fileType: selectedType.value,
      page: page.value,
      pageSize: pageSize.value,
      signal: current.signal,
    });
    if (current.signal.aborted) return;
    results.value = body.results;
    total.value = body.total;
    error.value = body.error ?? "";
    if (selectedId.value && !results.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = null;
    }
  } catch (err) {
    if (current.signal.aborted) return;
    error.value = err instanceof Error ? err.message : t("search.catalogError");
    results.value = [];
    total.value = 0;
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}

onMounted(async () => {
  await loadCatalog();
  if (session.user) void run(1);
});

watch(
  () => session.user,
  (user) => {
    if (user) {
      void loadCatalog();
      void run(1);
    }
  },
);
</script>

<style scoped>
.qka-search {
  height: calc(100vh - 50px);
  display: flex;
  flex-direction: column;
}
.qka-split,
.qka-mobile-preview {
  min-height: 0;
  flex: 1;
}
.qka-results {
  height: 100%;
  min-height: 0;
}
.qka-table {
  height: 100%;
}
.qka-search-catalog {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.qka-search-source {
  padding: 0.75rem 1rem;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 10px;
}
.qka-type-excel :deep(.q-icon),
.qka-type-excel {
  color: #217346;
}
.qka-type-word :deep(.q-icon),
.qka-type-word {
  color: #2b579a;
}
.qka-type-pdf :deep(.q-icon),
.qka-type-pdf {
  color: #c0392b;
}
.qka-type-powerpoint :deep(.q-icon),
.qka-type-powerpoint {
  color: #c43e1c;
}
.qka-type-csv :deep(.q-icon),
.qka-type-csv {
  color: #0d7377;
}
.qka-type-active {
  font-weight: 600;
}
.qka-actions-cell {
  width: 48px;
  padding-left: 0;
  padding-right: 4px;
}
.qka-open-original {
  opacity: 0.72;
}
.qka-open-original:hover,
.qka-open-original:focus-visible {
  opacity: 1;
}
.body--dark .qka-search-source {
  border-color: rgba(255, 255, 255, 0.16);
}
</style>
