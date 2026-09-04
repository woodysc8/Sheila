"""
Vector search over your uploaded documents (resume, org chart, client
roster, finance books, etc.) using local embeddings + ChromaDB -- so a
question only pulls in the few relevant paragraphs, not entire documents.
This is what keeps token usage sane as your document library grows.

Run ingest.py after adding/updating files in knowledge/ (e.g. User Background,
StreetCred Sourcebook_MASTER).
"""

import os
import chromadb
from google import genai
import config

_clients = [genai.Client(api_key=k) for k in config.GEMINI_API_KEYS if k and "PUT_YOUR" not in k]
_chroma = chromadb.PersistentClient(path=os.path.join(os.path.dirname(__file__), "data", "chroma"))
_collection = _chroma.get_or_create_collection("iris_knowledge")

EMBEDDING_MODEL = "gemini-embedding-001"


def _embed(text: str) -> list:
    """Tries each available Gemini key in turn -- mirrors brain.py's
    resilience, since embedding calls were previously silently failing
    whenever the first key happened to be exhausted."""
    last_error = None
    for client in _clients:
        try:
            result = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
            return result.embeddings[0].values
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"All Gemini keys failed for embedding: {last_error}")


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list:
    """Simple sliding-window chunking. Good enough for resumes/org charts/
    client lists -- for longer books, larger chunk_size (e.g. 1500) reads
    more naturally since it keeps more surrounding context together."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if c.strip()]


def add_document(doc_id: str, text: str, source_name: str):
    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks):
        try:
            embedding = _embed(chunk)
            _collection.upsert(
                ids=[f"{doc_id}_{i}"],
                embeddings=[embedding],
                documents=[chunk],
                metadatas=[{"source": source_name}],
            )
        except Exception as e:
            print(f"[knowledge] Failed to embed chunk {i} of {source_name}: {e}")
    print(f"[knowledge] Indexed {len(chunks)} chunks from {source_name}")


def ingest_document(doc_id: str, text: str, source_name: str):
    """Backend-neutral name for document ingestion."""
    return add_document(doc_id, text, source_name)


def query(question: str, top_k: int = 4) -> str:
    """Returns the most relevant chunks as a formatted string, or "" if
    nothing's indexed yet / the query fails. Called from brain.py on every
    request -- this DOES cost one embedding call per question, but embedding
    calls are far cheaper than sending whole documents as context."""
    try:
        if _collection.count() == 0:
            return ""
        embedding = _embed(question)
        results = _collection.query(query_embeddings=[embedding], n_results=top_k)
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        if not docs:
            return ""
        lines = [f"[from {m.get('source', 'unknown')}]: {d}" for d, m in zip(docs, metas)]
        return "\n\n".join(lines)
    except Exception as e:
        print(f"[knowledge] Query failed: {e}")
        return ""


def search_documents(question: str, top_k: int = 4) -> str:
    """Backend-neutral name for document retrieval."""
    return query(question, top_k=top_k)
