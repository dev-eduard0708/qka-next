<template>
  <q-page class="qka-docs">
    <div class="q-pa-md">
      <h1 class="text-h5 q-mb-sm">{{ t("documents.title") }}</h1>
      <p class="text-caption">{{ t("documents.paginated") }}</p>
    </div>
    <q-splitter v-model="outer" :limits="[28, 72]" class="qka-split">
      <template #before>
        <q-splitter v-model="inner" horizontal :limits="[35, 80]" style="height: 100%">
          <template #before>
            <div class="q-pa-sm">
              <q-input v-model="filter" dense outlined :label="t('documents.filter')" clearable @keyup.enter="load" />
              <q-list separator>
                <q-item
                  v-for="item in items"
                  :key="item.id"
                  clickable
                  v-ripple
                  :active="selected.includes(item.id)"
                  @click="toggle(item.id)"
                >
                  <q-item-section avatar>
                    <q-icon :name="iconFor(item.fileType)" />
                  </q-item-section>
                  <q-item-section>
                    <q-item-label>{{ item.fileName }}</q-item-label>
                    <q-item-label caption>{{ item.relativePath }}</q-item-label>
                  </q-item-section>
                </q-item>
              </q-list>
              <div v-if="!items.length" class="text-body2 q-pa-md">{{ error || t("documents.empty") }}</div>
            </div>
          </template>
          <template #after>
            <div class="q-pa-md">
              <div class="text-subtitle2 q-mb-sm">{{ t("documents.preview") }}</div>
              <pre class="qka-preview">{{ preview || "—" }}</pre>
              <q-btn
                v-if="hasMore"
                dense
                outline
                class="q-mt-sm"
                label="Load more"
                @click="loadMore"
              />
            </div>
          </template>
        </q-splitter>
      </template>
      <template #after>
        <div class="q-pa-md column full-height">
          <AiChatPanel
            :title="t('documents.chat')"
            :document-ids="selected"
            :attachment-labels="attachmentLabels"
            @remove-attachment="onRemoveAttachment"
          />
        </div>
      </template>
    </q-splitter>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AiChatPanel from "@/components/AiChatPanel.vue";
import { api } from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const filter = ref("");
const items = ref<Array<{ id: string; fileName: string; relativePath: string; fileType: string }>>([]);
const selected = ref<string[]>([]);
const preview = ref("");
const offset = ref(0);
const hasMore = ref(false);
const error = ref("");
const outer = ref(70);
const inner = ref(48);

const attachmentLabels = computed(() =>
  selected.value.map((id) => ({
    key: id,
    id,
    kind: "document" as const,
    label: titleFor(id),
  })),
);

function iconFor(type: string) {
  if (type === "excel" || type === "csv") return "table_chart";
  if (type === "pdf") return "picture_as_pdf";
  if (type === "word") return "description";
  if (type === "powerpoint") return "slideshow";
  return "insert_drive_file";
}

function titleFor(id: string) {
  return items.value.find((item) => item.id === id)?.fileName ?? id;
}

function onRemoveAttachment(att: { id: string }) {
  selected.value = selected.value.filter((id) => id !== att.id);
}

async function load() {
  if (!session.user) {
    items.value = [];
    return;
  }
  const result = await api.documents(filter.value);
  items.value = result.items;
  error.value = result.error ?? "";
}

async function show(id: string) {
  offset.value = 0;
  const detail = await api.document(id, 0);
  preview.value = detail.denied ? detail.error ?? "Access denied." : detail.text ?? "";
  hasMore.value = Boolean(detail.hasMore);
}

async function loadMore() {
  const id = selected.value[0];
  if (!id) return;
  offset.value += 4000;
  const detail = await api.document(id, offset.value);
  preview.value += detail.text ?? "";
  hasMore.value = Boolean(detail.hasMore);
}

function toggle(id: string) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((item) => item !== id)
    : [...selected.value, id];
  void show(id);
}

onMounted(load);
watch(() => session.user, load);
</script>

<style scoped>
.qka-docs {
  height: calc(100vh - 50px);
  display: flex;
  flex-direction: column;
}
.qka-split {
  flex: 1;
  min-height: 520px;
}
.qka-preview {
  white-space: pre-wrap;
  font-family: Consolas, "Courier New", monospace;
  font-size: 0.85rem;
  max-height: 320px;
  overflow: auto;
}
</style>
