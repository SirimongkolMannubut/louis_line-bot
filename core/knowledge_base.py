from __future__ import annotations

import os
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

KB_DIR = Path(__file__).resolve().parent.parent / "memory" / "knowledge_base"
KB_DIR.mkdir(parents=True, exist_ok=True)

_client = chromadb.PersistentClient(path=str(KB_DIR))
_ef     = embedding_functions.DefaultEmbeddingFunction()


def _collection(user_id: str):
    safe = user_id.replace(":", "_").replace("/", "_")
    return _client.get_or_create_collection(
        name=f"kb_{safe}", embedding_function=_ef
    )


def add_document(user_id: str, doc_id: str, text: str, metadata: dict | None = None) -> None:
    col = _collection(user_id)
    # แบ่ง text เป็น chunks ขนาด 500 ตัวอักษร
    chunks = [text[i:i+500] for i in range(0, len(text), 500)]
    ids    = [f"{doc_id}_{i}" for i in range(len(chunks))]
    metas  = [{**(metadata or {}), "doc_id": doc_id, "chunk": i} for i in range(len(chunks))]
    col.upsert(documents=chunks, ids=ids, metadatas=metas)


def search(user_id: str, query: str, n: int = 3) -> list[str]:
    try:
        col = _collection(user_id)
        if col.count() == 0:
            return []
        results = col.query(query_texts=[query], n_results=min(n, col.count()))
        return results["documents"][0] if results["documents"] else []
    except Exception:
        return []


def delete_document(user_id: str, doc_id: str) -> None:
    col = _collection(user_id)
    existing = col.get(where={"doc_id": doc_id})
    if existing["ids"]:
        col.delete(ids=existing["ids"])


def list_documents(user_id: str) -> list[str]:
    try:
        col  = _collection(user_id)
        data = col.get()
        seen = set()
        docs = []
        for m in data.get("metadatas", []):
            doc_id = m.get("doc_id", "")
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                docs.append(doc_id)
        return docs
    except Exception:
        return []


def ask_with_knowledge(user_id: str, question: str, ask_ai_fn) -> str:
    chunks = search(user_id, question)
    if not chunks:
        return ask_ai_fn(question, user_id=user_id)
    context = "\n\n".join(chunks)
    prompt  = (
        f"ใช้ข้อมูลต่อไปนี้เพื่อตอบคำถาม:\n\n{context}\n\n"
        f"คำถาม: {question}\n\n"
        f"ถ้าข้อมูลไม่เพียงพอ ให้บอกตามจริง"
    )
    return ask_ai_fn(prompt, user_id=user_id)
