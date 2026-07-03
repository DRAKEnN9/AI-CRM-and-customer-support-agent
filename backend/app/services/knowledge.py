import logging
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from litellm import embedding
from app.config import settings
from app.models.knowledge import KnowledgeDocument

logger = logging.getLogger(__name__)

def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """Splits text into overlapping chunks for better RAG retrieval."""
    chunks = []
    for i in range(0, len(text), chunk_size - chunk_overlap):
        chunks.append(text[i : i + chunk_size])
    return chunks

async def ingest_document(db: AsyncSession, text: str, source: str = "manual"):
    """Chunks, embeds, and stores a document in the knowledge base."""
    chunks = chunk_text(text)

    # Batch embedding for efficiency
    emb_response = embedding(model=settings.EMBEDDING_MODEL, input=chunks)
    embeddings = emb_response.data # This is usually a list of embedding objects

    for i, chunk in enumerate(chunks):
        vector = embeddings[i]["embedding"]
        doc = KnowledgeDocument(
            content=chunk,
            embedding=vector,
            source=source,
            active=True
        )
        db.add(doc)

    await db.commit()
    logger.info(f"Ingested {len(chunks)} chunks from source: {source}")

async def search_knowledge(db: AsyncSession, query_vector: List[float], top_k: int = 5) -> List[str]:
    """RAG retrieval: finds documents most similar to the provided embedding using cosine distance."""
    # Cosine distance operator is <=> in pgvector
    # We use a raw SQL fragment here because SQLAlchemy's distance methods can be tricky with cosine
    result = await db.execute(
        select(KnowledgeDocument.content)
        .where(KnowledgeDocument.active == True)
        .order_by(KnowledgeDocument.embedding.cosine_distance(query_vector))
        .limit(top_k)
    )
    return result.scalars().all()

