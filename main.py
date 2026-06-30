from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core import extract_video_id, answer_question

app = FastAPI(title="YouTube RAG Chatbot API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    url: str
    question: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(data: ChatRequest):
    video_id = extract_video_id(data.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="That doesn't look like a valid YouTube URL.")

    answer, citations = answer_question(video_id, data.question)
    return {"answer": answer, "citations": citations}