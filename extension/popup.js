// popup.js
const API_BASE_URL = "https://youtube-chatbot-using-langchain-dw3d.onrender.com";

const YOUTUBE_VIDEO_PATTERN =
  /^https?:\/\/(www\.)?(youtube\.com\/watch\?.*v=|youtu\.be\/|youtube\.com\/shorts\/)/;

let currentUrl = "";

document.addEventListener("DOMContentLoaded", init);
document.getElementById("ask").addEventListener("click", askQuestion);
document.getElementById("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) askQuestion();
});

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentUrl = tab?.url || "";
  const statusEl = document.getElementById("video-status");
  if (YOUTUBE_VIDEO_PATTERN.test(currentUrl)) {
    statusEl.textContent = "Ready - ask away.";
    statusEl.className = "status ok";
  } else {
    statusEl.textContent = "Open a YouTube video to use this.";
    statusEl.className = "status warn";
  }
}

async function askQuestion() {
  const questionEl = document.getElementById("question");
  const answerEl = document.getElementById("answer");
  const askBtn = document.getElementById("ask");
  const question = questionEl.value.trim();

  if (!question) return;

  if (!YOUTUBE_VIDEO_PATTERN.test(currentUrl)) {
    answerEl.innerHTML = `<p class="error">Open a YouTube video first.</p>`;
    return;
  }

  askBtn.disabled = true;
  askBtn.textContent = "Thinking...";

  try {
    // STEP 1: Ask content.js (running inside YouTube page) for the transcript.
    // This never hits a cloud server -- it's the browser reading YouTube's own
    // page data. YouTube can't block it.
    answerEl.innerHTML = `<p class="loading">Fetching transcript from YouTube...</p>`;

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const transcriptResult = await chrome.tabs.sendMessage(tab.id, {
      type: "GET_TRANSCRIPT",
    });

    if (transcriptResult?.error) {
      throw new Error(transcriptResult.error);
    }

    // STEP 2: Send transcript segments + question to your backend.
    // Backend does chunking, embedding, FAISS, retrieval, Gemini -- all the
    // heavy AI work. It just never has to touch YouTube's servers itself.
    answerEl.innerHTML = `<p class="loading">Thinking about your question...</p>`;

    const res = await fetch(`${API_BASE_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: currentUrl,
        question,
        segments: transcriptResult.segments, // <-- new field
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error (${res.status})`);
    }

    renderAnswer(await res.json());

  } catch (err) {
    answerEl.innerHTML = `<p class="error">${escapeHtml(
      err.message || "Something went wrong."
    )}</p>`;
  } finally {
    askBtn.disabled = false;
    askBtn.textContent = "Ask";
  }
}

function renderAnswer(data) {
  const answerEl = document.getElementById("answer");
  let html = `<p class="answer-text">${escapeHtml(data.answer || "")}</p>`;
  if (data.citations && data.citations.length > 0) {
    html += `<div class="citations"><strong>Mentioned at:</strong><ul>`;
    for (const c of data.citations) {
      html += `<li><a href="${c.url}" target="_blank" rel="noopener">${escapeHtml(c.time)}</a></li>`;
    }
    html += `</ul></div>`;
  }
  answerEl.innerHTML = html;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}