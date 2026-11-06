<script setup>
import { ref } from "vue";
import { api, fmtTime } from "../api.js";

const props = defineProps({
  meetings: { type: Array, required: true },
  selectedIds: { type: Array, required: true },
  activeId: { type: String, default: null },
  health: { type: Object, default: null },
});
const emit = defineEmits(["update:selectedIds", "open", "changed", "error"]);

const busy = ref("");
const dragging = ref(false);
const fileInput = ref(null);

function toggle(id) {
  const set = new Set(props.selectedIds);
  set.has(id) ? set.delete(id) : set.add(id);
  emit("update:selectedIds", [...set]);
}

async function run(label, fn) {
  busy.value = label;
  try {
    await fn();
    emit("changed");
  } catch (e) {
    emit("error", e.message);
  } finally {
    busy.value = "";
  }
}

const upload = (files) =>
  files?.length && run(`Analysing ${files.length} file(s)…`, () => api.upload([...files]));
const loadSamples = () => run("Loading sample meetings…", api.loadSamples);
const remove = (m) => confirm(`Delete "${m.title}"?`) && run("Deleting…", () => api.remove(m.id));

function onDrop(e) {
  dragging.value = false;
  upload(e.dataTransfer.files);
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="logo">🎙</span>
      <div>
        <strong>Meeting Intelligence</strong>
        <small>Ask your meetings anything</small>
      </div>
    </div>

    <div
      class="dropzone"
      :class="{ dragging }"
      @dragover.prevent="dragging = true"
      @dragleave="dragging = false"
      @drop.prevent="onDrop"
      @click="fileInput.click()"
    >
      <input ref="fileInput" type="file" multiple accept=".txt,.vtt" hidden @change="upload($event.target.files)" />
      <p><strong>Drop transcripts</strong> or click to upload</p>
      <small>.txt with "[hh:mm:ss] Speaker: text" lines, or .vtt</small>
    </div>
    <button class="ghost wide" :disabled="!!busy" @click="loadSamples">Load 3 sample meetings</button>
    <p v-if="busy" class="busy"><span class="spinner" /> {{ busy }}</p>

    <h3>
      Meetings
      <small v-if="meetings.length">{{ selectedIds.length ? `${selectedIds.length} in scope` : "all in scope" }}</small>
    </h3>
    <p v-if="!meetings.length" class="empty">No meetings yet. Upload a transcript or load the samples.</p>
    <ul class="meeting-list">
      <li v-for="m in meetings" :key="m.id" :class="{ active: m.id === activeId }">
        <input
          type="checkbox"
          :checked="selectedIds.includes(m.id)"
          :title="'Limit questions to selected meetings'"
          @change="toggle(m.id)"
        />
        <button class="meeting" @click="emit('open', m.id)">
          <span class="title">{{ m.title }}</span>
          <span class="meta">
            {{ m.date ?? "undated" }} · {{ fmtTime(m.duration_seconds) }} · {{ m.attendees.length }} people
            <span v-if="m.insights" class="pill">{{ m.insights.action_items.length }} actions</span>
          </span>
        </button>
        <button class="icon" title="Delete meeting" @click="remove(m)">✕</button>
      </li>
    </ul>

    <footer v-if="health" class="sidebar-footer">
      Embeddings: {{ health.embedder }}<br />
      Retrieval: hybrid (BM25 + vector, RRF)
    </footer>
  </aside>
</template>
