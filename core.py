import os
from urllib.parse import urlparse, parse_qs
from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
from youtube_transcript_api import (
    YouTubeTranscriptApi,
    NoTranscriptFound,
    TranscriptsDisabled,
)

from dotenv import load_dotenv
load_dotenv()

# EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
# embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

_embeddings = None

def get_embeddings():
    global _embeddings

    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL
        )

    return _embeddings

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
model = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GOOGLE_API_KEY,
)

# In-memory cache: video_id -> FAISS vector store.
_video_index_cache: dict = {}

PROMPT = PromptTemplate(
    template="""
You are a helpful AI assistant.

Your primary source of information is the provided YouTube transcript.

Rules:
1. Answer questions using the transcript whenever the answer is available.
2. If the user explicitly asks for information beyond the video (for example: "explain beyond this video", "what else do you know", "tell me more", "latest information", "real-world examples", "expand on this topic", etc.), you may use your general knowledge. Clearly mention that the additional information is **not from the video transcript**.
3. If the transcript does not contain the answer and the user did NOT ask for information beyond the video, respond:
   "I couldn't find that information in the provided video transcript."
4. Never invent or assume details that are not supported by the transcript when answering transcript-based questions.
5. When your answer combines transcript information with general knowledge, clearly separate the two sections.

Transcript:
{context}

Question:
{question}

Answer:
""",
    input_variables=["context", "question"],
)

parser = StrOutputParser()


# 1. Extract the transcript : -

def extract_video_id(url: str):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        valid_domains = {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "www.youtu.be",
        }

        if domain not in valid_domains:
            return None

        if "youtu.be" in domain:
            video_id = parsed.path.strip("/")
        elif parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/")[2]
        elif parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/")[2]
        else:
            video_id = None

        return video_id or None

    except Exception:
        return None


def get_transcript_segments(video_id: str):
    api = YouTubeTranscriptApi()

    try:
        transcript_list = api.list(video_id)

        try:
            transcript = transcript_list.find_transcript(["en", "en-US", "en-GB"])
        except NoTranscriptFound:
            transcript = next(iter(transcript_list))
            if transcript.is_translatable:
                transcript = transcript.translate("en")

        fetched = transcript.fetch()
        return [{"text": item.text, "start": item.start} for item in fetched]

    except TranscriptsDisabled:
        return None
    except NoTranscriptFound:
        return None


# 2. Split the transcript (Custom chunker that preserves the timestamp):-

def chunk_transcript_with_timestamps(segments, chunk_size=1200, chunk_overlap=200):

    chunks = []
    current_text = ""
    current_start = None

    for seg in segments:
        if current_start is None:
            current_start = seg["start"]

        current_text = f"{current_text} {seg['text']}".strip()

        if len(current_text) >= chunk_size:
            chunks.append({"text": current_text, "start": current_start})
            current_text = current_text[-chunk_overlap:] if chunk_overlap > 0 else ""
            current_start = None  # next segment processed will set the new start

    if current_text:
        fallback_start = current_start if current_start is not None else segments[-1]["start"]
        chunks.append({"text": current_text, "start": fallback_start})

    return chunks


# 3. Index build + cache

def get_or_build_index(video_id: str):
    if video_id in _video_index_cache:
        return _video_index_cache[video_id]

    segments = get_transcript_segments(video_id)
    if not segments:
        return None

    chunks = chunk_transcript_with_timestamps(segments)
    docs = [
        Document(page_content=c["text"], metadata={"start": c["start"]})
        for c in chunks
    ]

    vector_store = FAISS.from_documents(documents=docs, embedding=embeddings)
    _video_index_cache[video_id] = vector_store
    return vector_store


# 4. Formatting + citation helpers

def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_docs_with_sources(docs):
    context_text = "\n\n".join(doc.page_content for doc in docs)
    starts = [doc.metadata.get("start") for doc in docs if doc.metadata.get("start") is not None]
    return context_text, starts


def build_citations(video_id: str, starts, limit: int = 3):
    seen = set()
    citations = []
    for start in starts:
        rounded = int(start)
        if rounded in seen:
            continue
        seen.add(rounded)
        citations.append({
            "time": format_timestamp(start),
            "seconds": rounded,
            "url": f"https://youtu.be/{video_id}?t={rounded}",
        })
        if len(citations) >= limit:
            break
    return citations


# 5. Full chain : retrieve -> prompt -> LLM -> parse

def answer_question(video_id: str, question: str):

    vector_store = get_or_build_index(video_id)
    if vector_store is None:
        return "I couldn't fetch a transcript for this video (it may have transcripts disabled).", []

    retriever = vector_store.as_retriever(
        search_type="mmr",
        search_kwargs={"k": 6, "fetch_k": 10, "lambda_mult": 0.9},
    )
    retrieved_docs = retriever.invoke(question)
    context_text, starts = format_docs_with_sources(retrieved_docs)

    # final_prompt = PROMPT.invoke({"context": context_text, "question": question})
    # result = model.invoke(final_prompt)
    # answer = parser.invoke(result)

    chain = PROMPT | model | parser
    answer = chain.invoke({
        "context": context_text,
        "question": question,
    })

    citations = build_citations(video_id, starts)
    return answer, citations