import os
from urllib.parse import urlparse, parse_qs
from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_community.vectorstores import FAISS
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Gemini embedding -- free tier, no local model, no RAM cost
embeddings = GoogleGenerativeAIEmbeddings(
    model="text-embedding-001",
    google_api_key=GOOGLE_API_KEY,
)

model = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GOOGLE_API_KEY,
)

_video_index_cache: dict = {}

PROMPT = PromptTemplate(
    template="""
You are a helpful AI assistant.
Your primary source of information is the provided YouTube transcript.

Rules:
1. Answer questions using the transcript whenever the answer is available.
2. If the user explicitly asks for information beyond the video (e.g. "explain beyond this video", "what else do you know", "tell me more"), you may use your general knowledge. Clearly mention it is **not from the video transcript**.
3. If the transcript does not contain the answer and the user did NOT ask for info beyond the video, respond: "I couldn't find that information in the provided video transcript."
4. Never invent details not in the transcript.
5. When combining transcript + general knowledge, clearly separate the two sections.

Transcript:
{context}

Question:
{question}

Answer:
""",
    input_variables=["context", "question"],
)

parser = StrOutputParser()


def extract_video_id(url: str):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        valid = {"youtube.com","www.youtube.com","m.youtube.com","youtu.be","www.youtu.be"}
        if domain not in valid:
            return None
        if "youtu.be" in domain:
            return parsed.path.strip("/") or None
        elif parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[2]
        elif parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]
        return None
    except Exception:
        return None


def chunk_transcript_with_timestamps(segments, chunk_size=1200, chunk_overlap=200):
    chunks, current_text, current_start = [], "", None
    for seg in segments:
        if current_start is None:
            current_start = seg["start"]
        current_text = f"{current_text} {seg['text']}".strip()
        if len(current_text) >= chunk_size:
            chunks.append({"text": current_text, "start": current_start})
            current_text = current_text[-chunk_overlap:] if chunk_overlap else ""
            current_start = None
    if current_text:
        chunks.append({"text": current_text, "start": current_start or segments[-1]["start"]})
    return chunks


def build_index_from_segments(video_id: str, segments: list):
    if video_id in _video_index_cache:
        return _video_index_cache[video_id]
    chunks = chunk_transcript_with_timestamps(segments)
    docs = [Document(page_content=c["text"], metadata={"start": c["start"]}) for c in chunks]
    vs = FAISS.from_documents(documents=docs, embedding=embeddings)
    _video_index_cache[video_id] = vs
    return vs


def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_citations(video_id: str, docs, limit=3):
    seen, citations = set(), []
    for doc in docs:
        start = doc.metadata.get("start")
        if start is None: continue
        r = int(start)
        if r in seen: continue
        seen.add(r)
        citations.append({"time": format_timestamp(start), "seconds": r,
                          "url": f"https://youtu.be/{video_id}?t={r}"})
        if len(citations) >= limit: break
    return citations


def answer_question(video_id: str, question: str, segments: list):
    vs = build_index_from_segments(video_id, segments)
    retriever = vs.as_retriever(
        search_type="mmr",
        search_kwargs={"k": 6, "fetch_k": 10, "lambda_mult": 0.9},
    )
    docs = retriever.invoke(question)
    context = "\n\n".join(d.page_content for d in docs)
    answer = (PROMPT | model | parser).invoke({"context": context, "question": question})
    return answer, build_citations(video_id, docs)