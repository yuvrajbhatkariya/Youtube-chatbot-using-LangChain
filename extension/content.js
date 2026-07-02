// Runs on every youtube.com page automatically (declared in manifest.json).
// When popup asks "GET_TRANSCRIPT", we read it from YouTube's own page data.

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "GET_TRANSCRIPT") {
    fetchTranscript()
      .then(sendResponse)
      .catch((err) => sendResponse({ error: err.message }));
    return true; // IMPORTANT: return true keeps the message channel open for async
  }
});

async function fetchTranscript() {
  // YouTube injects ytInitialPlayerResponse into every watch page as a
  // global JS variable. It contains everything about the video including
  // caption track URLs. We never make a server-side request.
  const playerResponse = window.ytInitialPlayerResponse;

  if (!playerResponse) {
    throw new Error("Could not find YouTube player data. Are you on a video page?");
  }

  const captions =
    playerResponse?.captions?.playerCaptionsTracklistRenderer?.captionTracks;

  if (!captions || captions.length === 0) {
    throw new Error("This video has no captions/subtitles available.");
  }

  // Prefer English track, fall back to first available
  const track =
    captions.find((t) => t.languageCode === "en") ||
    captions.find((t) => t.languageCode?.startsWith("en")) ||
    captions[0];

  // Fetch the caption XML -- this is a browser request to youtube.com,
  // never blocked because it's coming from a real user's browser.
  const res = await fetch(track.baseUrl);
  if (!res.ok) throw new Error(`Failed to fetch captions (${res.status})`);
  const xml = await res.text();

  return { segments: parseCaptionXml(xml) };
}

function parseCaptionXml(xml) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xml, "text/xml");
  const nodes = doc.querySelectorAll("text");
  const segments = [];

  nodes.forEach((node) => {
    const start = parseFloat(node.getAttribute("start") || "0");
    const raw = node.textContent || "";
    // YouTube encodes HTML entities in captions (&#39; etc), decode them
    const text = new DOMParser()
      .parseFromString(raw, "text/html")
      .documentElement.textContent.trim();
    if (text) segments.push({ text, start });
  });

  return segments;
}