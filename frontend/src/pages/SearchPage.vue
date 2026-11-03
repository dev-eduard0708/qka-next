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
            <q-chip v-for="ft in source.types" :key="ft.key" outline dense :class="`qka-type-${ft.key}`">
              <q-icon :name="ft.icon" size="18px" class="q-mr-xs" />
              <span>{{ ft.label }}</span>
              <q-badge rounded :color="badgeColor(ft.key)" class="q-ml-sm">{{ ft.count }}</q-badge>
            </q-chip>
          </div>
        </div>
      </div>
      <div v-else-if="catalogLoaded && !catalog.error" class="text-caption text-grey-7 q-mb-sm">
        {{ t("search.noSources") }}
      </div>
      <div class="row q-col-gutter-sm">
        <div class="col">
          <q-input v-model="query" outlined dense :label="t('search.query')" @keyup.enter="run" />
        </div>
        <div class="col-auto">
          <q-btn color="primary" unelevated :label="t('search.run')" :disable="!session.user" @click="run" />
        </div>
      </div>
      <q-banner v-if="catalog.error" class="bg-negative text-white q-mt-sm">{{ catalog.error }}</q-banner>
      <q-banner v-if="error" class="bg-negative text-white q-mt-sm">{{ error }}</q-banner>
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
        <div class="qka-results">
          <q-list v-if="results.length" separator>
            <q-item
              v-for="hit in results"
              :key="hit.id"
              clickable
              v-ripple
              :active="selectedId === hit.id"
              @click="selectHit(hit)"
            >
              <q-item-section avatar>
                <q-icon :name="iconFor(hit.fileType)" :class="`qka-type-${hit.fileType}`" />
              </q-item-section>
              <q-item-section>
                <q-item-label>{{ hit.title }}</q-item-label>
                <q-item-label caption>{{ folderOf(hit.relativePath) }}</q-item-label>
                <q-item-label caption>{{ hit.snippet }}</q-item-label>
                <q-item-label caption>{{ formatSize(hit.sizeBytes) }} · {{ hit.fileType }}</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else-if="ran" class="text-body2 q-pa-md">{{ t("search.none") }}</div>
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
import { computed, onMounted, ref } from "vue";
import { useQuasar } from "quasar";
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
  extension?: string;
  sizeBytes?: number;
};

const SPLITTER_KEY = "qka.search.splitter";
const { t } = useI18n();
const $q = useQuasar();
const session = useSessionStore();
const query = ref("Laptop");
const results = ref<SearchHit[]>([]);
const error = ref("");
const ran = ref(false);
const selectedId = ref<string | null>(null);
const splitter = ref(Number(localStorage.getItem(SPLITTER_KEY) || 40));
const catalogLoaded = ref(false);
const catalog = ref<{ sources: CatalogSource[]; totalFiles: number; error: string | null }>({
  sources: [],
  totalFiles: 0,
  error: null,
});

const selectedHit = computed(() => results.value.find((item) => item.id === selectedId.value));

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

function folderOf(path: string | undefined) {
  if (!path) return "";
  const parts = path.replace(/\//g, "\\").split("\\");
  return parts.length > 1 ? parts.slice(0, -1).join("\\") : path;
}

function formatSize(bytes: number | undefined) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 102.4) / 10} KB`;
  return `${Math.round(value / 104857.6) / 10} MB`;
}

function selectHit(hit: SearchHit) {
  selectedId.value = hit.id;
}

function closePreview() {
  selectedId.value = null;
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

async function run() {
  ran.value = true;
  const body = await api.search(query.value);
  results.value = body.results;
  error.value = body.error ?? "";
  if (selectedId.value && !results.value.some((item) => item.id === selectedId.value)) {
    selectedId.value = null;
  }
}

onMounted(() => {
  void loadCatalog();
});
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
  overflow: auto;
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
</style>
