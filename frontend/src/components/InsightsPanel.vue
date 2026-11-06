<script setup>
import { onMounted, ref, watch } from "vue";
import { api, parseTs } from "../api.js";

const props = defineProps({
  meeting: { type: Object, default: null },
  meetings: { type: Array, required: true },
});
const emit = defineEmits(["jump", "changed", "error"]);

const view = ref("meeting"); // meeting | all-actions
const allActions = ref([]);
const regenerating = ref(false);

async function loadAllActions() {
  try {
    allActions.value = await api.actionItems();
  } catch (e) {
    emit("error", e.message);
  }
}

function jump(meetingId, ts) {
  const start = parseTs(ts);
  if (start !== null) emit("jump", { meeting_id: meetingId, start, end: start + 30 });
}

async function regenerate() {
  regenerating.value = true;
  try {
    await api.regenerateInsights(props.meeting.id);
    emit("changed");
  } catch (e) {
    emit("error", e.message);
  } finally {
    regenerating.value = false;
  }
}

watch(view, (v) => v === "all-actions" && loadAllActions());
watch(() => props.meetings, () => view.value === "all-actions" && loadAllActions());
onMounted(() => view.value === "all-actions" && loadAllActions());
</script>

<template>
  <section class="panel insights">
    <div class="segmented">
      <button :class="{ active: view === 'meeting' }" @click="view = 'meeting'">This meeting</button>
      <button :class="{ active: view === 'all-actions' }" @click="view = 'all-actions'">All action items</button>
    </div>

    <template v-if="view === 'meeting'">
      <p v-if="!meeting" class="empty">Select a meeting in the sidebar.</p>
      <template v-else>
        <div class="insights-head">
          <div>
            <h2>{{ meeting.title }}</h2>
            <p class="muted">{{ meeting.date }} · {{ meeting.attendees.join(", ") }}</p>
          </div>
          <button class="ghost" :disabled="regenerating" @click="regenerate">
            {{ regenerating ? "Re-analysing…" : "Re-analyse" }}
          </button>
        </div>

        <p v-if="!meeting.insights" class="empty">No insights yet - click Re-analyse.</p>
        <template v-else>
          <article class="card">
            <h3>Summary</h3>
            <p>{{ meeting.insights.summary }}</p>
          </article>

          <article class="card">
            <h3>Decisions <span class="count">{{ meeting.insights.decisions.length }}</span></h3>
            <ul class="items">
              <li v-for="(d, i) in meeting.insights.decisions" :key="i">
                <button class="ts" @click="jump(meeting.id, d.timestamp)">{{ d.timestamp }}</button>
                <span>{{ d.decision }} <em class="muted">— {{ d.made_by }}</em></span>
              </li>
            </ul>
          </article>

          <article class="card">
            <h3>Action items <span class="count">{{ meeting.insights.action_items.length }}</span></h3>
            <table class="grid">
              <thead><tr><th>When</th><th>Task</th><th>Owner</th><th>Due</th></tr></thead>
              <tbody>
                <tr v-for="(a, i) in meeting.insights.action_items" :key="i">
                  <td><button class="ts" @click="jump(meeting.id, a.timestamp)">{{ a.timestamp }}</button></td>
                  <td>{{ a.task }}</td>
                  <td><span class="owner">{{ a.owner }}</span></td>
                  <td>{{ a.due }}</td>
                </tr>
              </tbody>
            </table>
          </article>

          <article v-if="meeting.insights.open_questions.length" class="card">
            <h3>Open questions &amp; risks</h3>
            <ul>
              <li v-for="(q, i) in meeting.insights.open_questions" :key="i">{{ q }}</li>
            </ul>
          </article>
        </template>
      </template>
    </template>

    <template v-else>
      <p v-if="!allActions.length" class="empty">No action items yet.</p>
      <table v-else class="grid card">
        <thead><tr><th>Owner</th><th>Task</th><th>Due</th><th>Meeting</th></tr></thead>
        <tbody>
          <tr v-for="(a, i) in allActions" :key="i">
            <td><span class="owner">{{ a.owner }}</span></td>
            <td>{{ a.task }}</td>
            <td>{{ a.due }}</td>
            <td>
              <button class="ts" @click="jump(a.meeting_id, a.timestamp)">{{ a.meeting_title }} · {{ a.timestamp }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>
