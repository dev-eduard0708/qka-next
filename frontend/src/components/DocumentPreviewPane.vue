<template>
  <div class="qka-preview-pane column full-height">
    <div v-if="!documentId" class="qka-preview-empty column flex-center text-grey-7">
      <q-icon name="preview" size="42px" class="q-mb-sm" />
      <div class="text-body2">{{ t("search.previewEmpty") }}</div>
    </div>
    <template v-else>
      <div class="qka-preview-toolbar row items-center no-wrap q-px-sm q-py-xs">
        <q-icon :name="iconFor(preview?.fileType || hitFileType)" size="22px" :class="`qka-type-${preview?.fileType || hitFileType}`" />
        <div class="q-ml-sm ellipsis" style="min-width: 0; flex: 1">
          <div class="text-subtitle2 ellipsis">{{ preview?.title || hitTitle }}</div>
          <div class="text-caption ellipsis">
            {{ preview?.fileType || hitFileType }}
            <span v-if="preview?.folder"> · {{ preview.folder }}</span>
          </div>
        </div>
        <q-btn v-if="kind === 'pdf_pages' || kind === 'image'" flat dense round icon="zoom_out" :aria-label="t('search.previewZoomOut')" @click="zoom = Math.max(60, zoom - 15)" />
        <q-btn v-if="kind === 'pdf_pages' || kind === 'image'" flat dense round icon="fit_screen" :aria-label="t('search.previewFit')" @click="zoom = 100" />
        <q-btn v-if="kind === 'pdf_pages' || kind === 'image'" flat dense round icon="zoom_in" :aria-label="t('search.previewZoomIn')" @click="zoom = Math.min(200, zoom + 15)" />
        <q-btn
          v-if="preview?.canDownload"
          flat
          dense
          no-caps
          icon="download"
          :label="t('search.download')"
          @click="tryDownload"
        />
        <q-btn flat dense round icon="close" :aria-label="t('search.previewClose')" @click="$emit('close')" />
      </div>
      <div v-if="preview?.previewNote" class="text-caption text-grey-7 q-px-sm q-pb-xs">{{ preview.previewNote }}</div>
      <q-linear-progress v-if="loading" indeterminate color="primary" />
      <q-banner v-if="banner" dense class="bg-negative text-white q-ma-sm">{{ banner }}</q-banner>
      <div class="col qka-preview-body">
        <div v-if="kind === 'excel_grid'" class="full-height column">
          <div class="row items-center q-gutter-xs q-pa-sm">
            <q-btn
              v-for="sheet in preview?.sheets || []"
              :key="sheet.index"
              dense
              unelevated
              :color="sheet.index === (preview?.sheet ?? 0) ? 'primary' : 'grey-4'"
              :text-color="sheet.index === (preview?.sheet ?? 0) ? 'white' : 'dark'"
              :label="sheet.name"
              @click="load({ sheet: sheet.index, rowOffset: 0 })"
            />
          </div>
          <div class="col qka-table-wrap">
            <table class="qka-grid">
              <tbody>
                <tr v-for="(row, r) in preview?.rows || []" :key="r">
                  <td class="qka-row-num">{{ (preview?.rowOffset || 0) + r + 1 }}</td>
                  <td v-for="(cell, c) in row" :key="c">{{ cell }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="row justify-end q-pa-sm">
            <q-btn
              v-if="preview?.hasMore"
              dense
              outline
              :label="t('search.previewMoreRows')"
              @click="load({ sheet: preview?.sheet, rowOffset: (preview?.rowOffset || 0) + (preview?.rowCount || 0) })"
            />
          </div>
        </div>
        <div v-else-if="kind === 'word_document'" class="qka-word q-pa-md">
          <template v-for="(block, i) in preview?.blocks || []" :key="i">
            <h2 v-if="block.type === 'heading' && (block.level || 1) <= 2" class="text-h6">{{ block.text }}</h2>
            <h3 v-else-if="block.type === 'heading'" class="text-subtitle1">{{ block.text }}</h3>
            <p v-else-if="block.type === 'p'" class="q-mb-sm">{{ block.text }}</p>
            <table v-else-if="block.type === 'table'" class="qka-grid q-mb-md">
              <tbody>
                <tr v-for="(row, r) in block.rows || []" :key="r">
                  <td v-for="(cell, c) in row" :key="c">{{ cell }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </div>
        <div v-else-if="kind === 'pdf_pages'" class="column full-height">
          <div class="row items-center justify-center q-gutter-sm q-pa-sm">
            <q-btn dense outline icon="chevron_left" :disable="(preview?.page || 1) <= 1" :aria-label="t('search.previewPrev')" @click="load({ page: (preview?.page || 1) - 1 })" />
            <div class="text-caption">{{ t("search.page") }} {{ preview?.page }} / {{ preview?.pageCount }}</div>
            <q-btn dense outline icon="chevron_right" :disable="(preview?.page || 1) >= (preview?.pageCount || 1)" :aria-label="t('search.previewNext')" @click="load({ page: (preview?.page || 1) + 1 })" />
          </div>
          <pre class="qka-text col" :style="{ fontSize: `${zoom / 100}rem` }">{{ preview?.text || "—" }}</pre>
        </div>
        <div v-else-if="kind === 'powerpoint_slides'" class="column full-height">
          <div class="row items-center justify-center q-gutter-sm q-pa-sm">
            <q-btn dense outline icon="chevron_left" :disable="(preview?.slide || 0) <= 0" :aria-label="t('search.previewPrev')" @click="load({ slide: (preview?.slide || 0) - 1 })" />
            <div class="text-caption">{{ t("search.slide") }} {{ (preview?.slide || 0) + 1 }} / {{ preview?.slideCount }}</div>
            <q-btn dense outline icon="chevron_right" :disable="(preview?.slide || 0) + 1 >= (preview?.slideCount || 1)" :aria-label="t('search.previewNext')" @click="load({ slide: (preview?.slide || 0) + 1 })" />
          </div>
          <div class="row col no-wrap" style="min-height: 0">
            <div class="qka-thumbs q-pa-xs">
              <q-btn
                v-for="thumb in preview?.thumbnails || []"
                :key="thumb.index"
                dense
                unelevated
                class="full-width q-mb-xs"
                :color="thumb.index === (preview?.slide ?? 0) ? 'primary' : 'grey-3'"
                :text-color="thumb.index === (preview?.slide ?? 0) ? 'white' : 'dark'"
                :label="thumb.title"
                @click="load({ slide: thumb.index })"
              />
            </div>
            <div class="col qka-slide q-pa-md">
              <div class="text-subtitle1 q-mb-sm">{{ preview?.title }}</div>
              <pre class="qka-text">{{ preview?.body || "—" }}</pre>
            </div>
          </div>
        </div>
        <div v-else-if="kind === 'image'" class="qka-image-wrap">
          <img :src="mediaSrc" :alt="preview?.title || hitTitle" :style="{ width: `${zoom}%`, maxWidth: 'none' }" />
        </div>
        <div v-else-if="kind === 'csv_table'" class="qka-table-wrap">
          <table class="qka-grid">
            <tbody>
              <tr v-for="(row, r) in preview?.rows || []" :key="r">
                <td v-for="(cell, c) in row" :key="c">{{ cell }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <pre v-else-if="kind === 'text' || kind === 'indexed_text'" class="qka-text q-pa-md">{{ preview?.text || "—" }}</pre>
        <div v-else-if="kind === 'unavailable' || kind === 'denied'" class="qka-preview-empty column flex-center text-grey-7">
          <q-icon name="insert_drive_file" size="42px" class="q-mb-sm" />
          <div class="text-body2">{{ preview?.previewNote || preview?.error || t("search.previewUnavailable") }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { api, type DocumentPreview } from "@/api";

const props = defineProps<{
  documentId: string | null;
  hitTitle?: string;
  hitFileType?: string;
}>();
defineEmits<{ close: [] }>();

const { t } = useI18n();
const preview = ref<DocumentPreview | null>(null);
const loading = ref(false);
const banner = ref("");
const zoom = ref(100);
let token = 0;
let abort: AbortController | null = null;

const kind = computed(() => preview.value?.previewKind || "");
const mediaSrc = computed(() =>
  props.documentId && kind.value === "image" ? `${api.documentPreviewMedia(props.documentId)}?t=${token}` : "",
);

function iconFor(type: string | undefined) {
  if (type === "excel" || type === "csv") return "table_chart";
  if (type === "pdf") return "picture_as_pdf";
  if (type === "word") return "description";
  if (type === "powerpoint") return "slideshow";
  if (type === "images") return "image";
  if (type === "text") return "article";
  return "insert_drive_file";
}

async function load(opts?: { sheet?: number; page?: number; slide?: number; rowOffset?: number }) {
  if (!props.documentId) return;
  abort?.abort();
  abort = new AbortController();
  const current = abort;
  loading.value = true;
  banner.value = "";
  try {
    const body = await api.documentPreview(props.documentId, { ...opts, signal: current.signal });
    if (current.signal.aborted) return;
    preview.value = body;
    if (body.denied) banner.value = body.error || t("search.previewDenied");
  } catch (err) {
    if (current.signal.aborted) return;
    banner.value = err instanceof Error ? err.message : t("search.previewFailed");
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}

async function tryDownload() {
  if (!props.documentId) return;
  const body = await api.documentDownload(props.documentId);
  banner.value = body.denied ? body.error || t("search.downloadDenied") : t("search.downloadReady");
}

watch(
  () => props.documentId,
  (id) => {
    preview.value = null;
    zoom.value = 100;
    banner.value = "";
    token += 1;
    if (id) void load();
  },
  { immediate: true },
);
</script>

<style scoped>
.qka-preview-pane {
  min-height: 0;
  background: rgba(255, 255, 255, 0.4);
}
.body--dark .qka-preview-pane {
  background: rgba(255, 255, 255, 0.03);
}
.qka-preview-toolbar {
  border-bottom: 1px solid rgba(0, 0, 0, 0.12);
}
.qka-preview-body {
  min-height: 0;
  overflow: auto;
}
.qka-preview-empty {
  min-height: 240px;
}
.qka-table-wrap,
.qka-image-wrap {
  overflow: auto;
  min-height: 0;
  flex: 1;
}
.qka-grid {
  border-collapse: collapse;
  font-size: 0.8rem;
  min-width: 100%;
}
.qka-grid td {
  border: 1px solid rgba(0, 0, 0, 0.12);
  padding: 4px 8px;
  white-space: nowrap;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.qka-row-num {
  color: #888;
  background: rgba(0, 0, 0, 0.04);
}
.qka-text {
  white-space: pre-wrap;
  font-family: Consolas, "Courier New", monospace;
  font-size: 0.85rem;
  margin: 0;
  overflow: auto;
}
.qka-word {
  max-width: 760px;
  margin: 0 auto;
}
.qka-thumbs {
  width: 160px;
  overflow: auto;
  border-right: 1px solid rgba(0, 0, 0, 0.12);
}
.qka-slide {
  overflow: auto;
}
.qka-type-excel {
  color: #217346;
}
.qka-type-word {
  color: #2b579a;
}
.qka-type-pdf {
  color: #c0392b;
}
.qka-type-powerpoint {
  color: #c43e1c;
}
</style>
