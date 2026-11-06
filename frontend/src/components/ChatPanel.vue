<script setup>
import { computed, nextTick, ref } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { api, fmtTime } from "../api.js";

const props = defineProps({
  meetings: { type: Array, required: true },
  selectedIds: { type: Array, required: true },
});
const emit = defineEmits(["cite"]);

const messages = ref([]); // { role, content, citations, meta, streaming }
const question = ref("");
const streaming = ref(false);
const thread = ref(null);
let controller = null;

const suggestions = [
  "What decisions were made across these meetings?",
  "List all action items for Arjun with due dates.",
  "What caused the payment outage and how was it detected?",
  "What is the target for checkout abandonment?",
];

const scopeLabel = computed(() => {
  if (!props.selectedIds.length) return `all ${props.meetings.length} meetings`;
  return props.meetings
    .filter((m) => props.selectedIds.includes(m.id))
    .map((m) => m.title)
    .join(", ");
});

// Turn [n] markers into clickable chips after sanitising the markdown.
function render(msg) {
  const html = DOMPurify.sanitize(marked.parse(msg.content || ""));
  if (!msg.citations?.length) return html;
  return html.replace(/\[(\d{1,2})\]/g, (whole, n) => {
    const c = msg.citations[Number(n) - 1];
    return c ? `<button class="cite" data-cite="${n}" title="${c.meeting_title} @ ${fmtTime(c.start)}">${n}</button>` : whole;
  });
}

function onAnswerClick(event, msg) {
  const n = event.target?.dataset?.cite;
  if (n) emit("cite", msg.citations[Number(n) - 1]);
}

async function scrollDown() {
  await nextTick();
  thread.value?.scrollTo({ top: thread.value.scrollHeight, behavior: "smooth" });
}

async function ask(text = question.value) {
  const q = text.trim();
  if (!q || streaming.value) return;
  question.value = "";
  const history = messages.value
    .filter((m) => !m.error)
    .slice(-6)
    .map(({ role, content }) => ({ role, content }));
  messages.value.push({ role: "user", content: q });
  const answer = { role: "assistant", content: "", citations: [], meta: null, streaming: true };
  messages.value.push(answer);
  const msg = messages.value[messages.value.length - 1];
  streaming.value = true;
  controller = new AbortController();
  scrollDown();
  try {
    await api.chat(
      { question: q, meeting_ids: props.selectedIds.length ? props.selectedIds : null, history },
      {
        signal: controller.signal,
        onEvent(event, data) {
          if (event === "retrieval") msg.citations = data.citations;
          else if (event === "delta") {
            msg.content += data.text;
            scrollDown();
          } else if (event === "done") {
            msg.content = data.answer; // server-validated version (invalid citation markers removed)
            msg.meta = data;
          } else if (event === "error") {
            msg.content = data.message;
            msg.error = true;
          }
        },
      },
    );
  } catch (e) {
    if (e.name !== "AbortError") {
      msg.content = `Request failed: ${e.message}`;
      msg.error = true;
    }
  } finally {
    msg.streaming = false;
    streaming.value = false;
    scrollDown();
  }
}

function stop() {
  controller?.abort();
}
</script>

<template>
  <section class="panel chat">
    <div ref="thread" class="thread">
      <div v-if="!messages.length" class="welcome">
        <h2>Ask about your meetings</h2>
        <p>
          Answers are grounded in the transcripts. Every claim carries a numbered citation you can click to jump to
          the exact moment in the meeting.
        </p>
        <div class="suggestions">
          <button v-for="s in suggestions" :key="s" :disabled="!meetings.length" @click="ask(s)">{{ s }}</button>
        </div>
        <p v-if="!meetings.length" class="empty">Load the sample meetings from the sidebar to try it.</p>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg" :class="[m.role, { error: m.error }]">
        <div v-if="m.role === 'user'" class="bubble">{{ m.content }}</div>
        <div v-else class="bubble answer">
          <div v-if="m.streaming && !m.content" class="typing"><span /><span /><span /></div>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="markdown" @click="onAnswerClick($event, m)" v-html="render(m)" />

          <div v-if="m.citations?.length && !m.streaming" class="sources">
            <span class="label">Sources</span>
            <button
              v-for="(c, n) in m.citations"
              :key="c.chunk_id"
              class="source"
              :class="{ cited: m.meta?.cited?.includes(n + 1) }"
              @click="emit('cite', c)"
            >
              <b>{{ n + 1 }}</b> {{ c.meeting_title }} · {{ fmtTime(c.start) }} · {{ c.speakers.join(", ") }}
            </button>
          </div>

          <details v-if="m.meta" class="trace">
            <summary>
              Retrieval details · {{ m.meta.latency_ms }} ms
              <span v-if="m.meta.ungrounded" class="warn">⚠ no citations</span>
              <span v-if="m.meta.refused" class="muted">low relevance → not answered</span>
            </summary>
            <table>
              <thead>
                <tr><th>#</th><th>Meeting</th><th>Time</th><th>Cosine</th><th>BM25 rank</th><th>Vector rank</th></tr>
              </thead>
              <tbody>
                <tr v-for="(c, n) in m.citations" :key="c.chunk_id">
                  <td>{{ n + 1 }}</td>
                  <td>{{ c.meeting_title }}</td>
                  <td>{{ fmtTime(c.start) }}</td>
                  <td>{{ c.score.toFixed(3) }}</td>
                  <td>{{ c.keyword_rank ?? "–" }}</td>
                  <td>{{ c.dense_rank ?? "–" }}</td>
                </tr>
              </tbody>
            </table>
            <p class="muted">
              Retrieval query: “{{ m.meta.retrieval_query }}” · model: {{ m.meta.model }} · trace {{ m.meta.trace_id }}
              <template v-if="m.meta.usage"> · tokens in {{ m.meta.usage.input_tokens }} / out {{ m.meta.usage.output_tokens }}</template>
            </p>
          </details>
        </div>
      </div>
    </div>

    <form class="composer" @submit.prevent="ask()">
      <small class="scope">Scope: {{ scopeLabel }}</small>
      <div class="row">
        <textarea
          v-model="question"
          rows="2"
          maxlength="1000"
          placeholder="e.g. Who owns the canary deployment and when is it due?"
          :disabled="!meetings.length"
          @keydown.enter.exact.prevent="ask()"
        />
        <button v-if="streaming" type="button" class="ghost" @click="stop">Stop</button>
        <button v-else type="submit" :disabled="!question.trim() || !meetings.length">Ask</button>
      </div>
    </form>
  </section>
</template>
