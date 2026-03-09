#!/usr/bin/env python3
"""
Real RAG API Service for Partner Agents.

This service provides RAG-based question answering using:
- ChromaDB for vector storage
- Google Gemini for embeddings and LLM
- Support ticket knowledge base
"""

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import chromadb
import numpy as np
import structlog
import uvicorn
from chromadb.config import Settings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google import genai

logger = structlog.get_logger()

# Configuration from environment
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-004")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash-exp")

if not GOOGLE_API_KEY:
    raise RuntimeError(
        "GOOGLE_API_KEY environment variable is required. "
        "Set it before starting the RAG service."
    )
genai_client = genai.Client(api_key=GOOGLE_API_KEY)
logger.info("Google GenAI configured", model=LLM_MODEL)


# Custom embedding function using new Google GenAI SDK
class GoogleGenAIEmbeddingFunction:
    """Custom embedding function using new google-genai package."""

    def __init__(self, client: genai.Client, model_name: str):
        self.client = client
        self.model_name = model_name

    def name(self) -> str:
        """Return the name of the embedding function."""
        return f"GoogleGenAI-{self.model_name}"

    def __call__(self, input: List[str]) -> List[np.ndarray]:
        """Generate embeddings for input texts (used when adding documents)."""
        embeddings = []
        for text in input:
            response = self.client.models.embed_content(
                model=self.model_name,
                contents=text
            )
            if not response.embeddings or not response.embeddings[0].values:
                raise ValueError(f"Empty embedding response for text: {text[:50]}...")
            embeddings.append(np.array(response.embeddings[0].values))
        return embeddings

    def embed_query(self, input: List[str]) -> List[np.ndarray]:
        """Generate embeddings for query texts (used when searching)."""
        return self.__call__(input)

# ChromaDB client
chroma_client: Optional[chromadb.HttpClient] = None
embedding_function: Optional[GoogleGenAIEmbeddingFunction] = None


def initialize_chromadb() -> None:
    """Initialize ChromaDB client and embedding function."""
    global chroma_client, embedding_function

    try:
        chroma_client = chromadb.HttpClient(
            host=CHROMA_HOST,
            port=CHROMA_PORT,
            settings=Settings(anonymized_telemetry=False)
        )

        embedding_function = GoogleGenAIEmbeddingFunction(
            client=genai_client,
            model_name=EMBEDDING_MODEL
        )

        logger.info("ChromaDB client initialized", host=CHROMA_HOST, port=CHROMA_PORT)
        logger.info("Embedding model configured", model=EMBEDDING_MODEL)

        collections = chroma_client.list_collections()
        logger.info("Available collections", names=[c.name for c in collections])

    except Exception as e:
        logger.error("Failed to initialize ChromaDB", error=str(e))
        raise


def get_collection(collection_name: str) -> Optional[chromadb.Collection]:
    """Get or create a ChromaDB collection by name."""
    try:
        collection = chroma_client.get_or_create_collection(
            name=collection_name,
            embedding_function=embedding_function,
            metadata={"hnsw:space": "cosine"}
        )
        return collection
    except Exception as e:
        logger.error("Failed to get collection", collection=collection_name, error=str(e))
        return None


def search_knowledge_base(
    query: str,
    collection_name: str = "support_tickets",
    num_results: int = 3,
    min_similarity: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Search the knowledge base for relevant documents.

    Args:
        query: User's search query
        collection_name: ChromaDB collection to search
        num_results: Number of results to return
        min_similarity: Minimum similarity threshold (0-1)

    Returns:
        List of relevant documents with metadata
    """
    try:
        collection = get_collection(collection_name)
        if not collection:
            logger.error("Collection not found", collection=collection_name)
            return []

        # Query the collection
        results = collection.query(
            query_texts=[query],
            n_results=num_results,
            include=["documents", "metadatas", "distances"]
        )

        # Format results
        documents = []
        if results and results['ids'] and len(results['ids'][0]) > 0:
            for i in range(len(results['ids'][0])):
                # ChromaDB returns distances (lower is better)
                # Convert to similarity score (higher is better)
                distance = results['distances'][0][i]
                similarity = 1 - (distance / 2)  # Cosine distance to similarity

                # Filter by minimum similarity
                if similarity >= min_similarity:
                    documents.append({
                        "id": results['ids'][0][i],
                        "content": results['documents'][0][i],
                        "metadata": results['metadatas'][0][i],
                        "similarity": similarity,
                        "distance": distance
                    })

        logger.info("Knowledge base search complete", result_count=len(documents), query_preview=query[:50])
        return documents

    except Exception:
        logger.exception("Error searching knowledge base")
        return []


def generate_answer(query: str, context_docs: List[Dict[str, Any]]) -> str:
    """
    Generate an answer using LLM with retrieved context.

    Args:
        query: User's question
        context_docs: Retrieved documents from knowledge base

    Returns:
        Generated answer
    """
    try:
        # Build context from retrieved documents
        context_parts = []
        for i, doc in enumerate(context_docs):
            metadata = doc.get('metadata', {})
            similarity = doc.get('similarity', 0)

            context_parts.append(
                f"[Document {i+1}] (Relevance: {similarity:.2f})\n"
                f"Ticket ID: {metadata.get('ticket_id', 'N/A')}\n"
                f"Category: {metadata.get('category', 'N/A')}\n"
                f"Content: {doc['content']}\n"
            )

        context = "\n---\n".join(context_parts)

        # Create prompt for LLM
        prompt = f"""You are a helpful IT support specialist. Answer the user's question based on the following support ticket history and documentation.

Retrieved Context:
{context}

User Question: {query}

Instructions:
- Provide a clear, actionable answer based on the retrieved context
- If the context contains a solution, explain it step-by-step
- If multiple solutions exist, mention the most relevant one
- If the context doesn't fully answer the question, say so and provide general guidance
- Keep your answer concise and practical

Answer:"""

        # Generate response using new Google GenAI SDK
        response = genai_client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt
        )

        answer = response.text.strip()
        logger.info("Generated answer", query_preview=query[:50])

        return answer

    except Exception as e:
        logger.error("Error generating answer", error=str(e))
        return "I encountered an error while generating a response. Please try again."


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize services on startup."""
    logger.info("Starting RAG API Service...")
    initialize_chromadb()
    logger.info("RAG API Service ready")
    yield


app = FastAPI(title="RAG API Service", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "RAG API Service",
        "version": "1.0.0",
        "description": "Real RAG-based question answering for partner agents",
        "endpoints": {
            "/answer": "POST - Query the RAG knowledge base",
            "/health": "GET - Health check",
            "/collections": "GET - List available collections",
            "/stats": "GET - Collection statistics"
        }
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    try:
        # Check ChromaDB connection
        collections = chroma_client.list_collections()

        return {
            "status": "healthy",
            "service": "rag-api",
            "version": "1.0.0",
            "chromadb": {
                "host": CHROMA_HOST,
                "port": CHROMA_PORT,
                "collections": len(collections)
            },
            "llm_model": LLM_MODEL,
            "embedding_model": EMBEDDING_MODEL
        }
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": "Service dependencies unavailable"
            }
        )


@app.get("/collections")
async def list_collections():
    """List available ChromaDB collections."""
    try:
        collections = chroma_client.list_collections()

        collection_info = []
        for coll in collections:
            try:
                count = coll.count()
                collection_info.append({
                    "name": coll.name,
                    "count": count,
                    "metadata": coll.metadata
                })
            except Exception as e:
                logger.error("Error getting collection info", collection=coll.name, error=str(e))

        return {
            "collections": collection_info,
            "total": len(collection_info)
        }
    except Exception as e:
        logger.error("Error listing collections", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to list collections"}
        )


@app.get("/stats/{collection_name}")
async def collection_stats(collection_name: str):
    """Get statistics for a specific collection."""
    try:
        collection = get_collection(collection_name)
        if not collection:
            return JSONResponse(
                status_code=404,
                content={"error": f"Collection {collection_name} not found"}
            )

        count = collection.count()

        # Get sample documents
        sample = collection.peek(limit=5)

        return {
            "name": collection_name,
            "document_count": count,
            "metadata": collection.metadata,
            "sample_ids": sample['ids'] if sample else []
        }
    except Exception as e:
        logger.error("Error getting collection stats", collection=collection_name, error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve collection statistics"}
        )


@app.post("/answer")
async def answer(request: Request):
    """
    Main RAG endpoint for answering questions.

    Request body:
    {
        "user_query": "My application crashes with error 500",
        "num_sources": 3,
        "only_high_similarity_nodes": false,
        "collection": "support_tickets",  # optional
        "min_similarity": 0.5  # optional
    }

    Response:
    {
        "response": "Generated answer...",
        "sources": [
            {
                "id": "doc-123",
                "content": "Document content...",
                "similarity": 0.85,
                "metadata": {...}
            }
        ],
        "metadata": {
            "num_sources": 3,
            "query_type": "software_support",
            "collection": "support_tickets"
        }
    }
    """
    try:
        body = await request.json()
        logger.info("Received RAG query", query_preview=body.get("user_query", "")[:100])

        user_query = body.get("user_query", "")
        num_sources = body.get("num_sources", 3)
        only_high_similarity = body.get("only_high_similarity_nodes", False)
        collection_name = body.get("collection", "support_tickets")
        min_similarity = body.get("min_similarity", 0.7 if only_high_similarity else 0.0)

        if not user_query:
            return JSONResponse(
                status_code=400,
                content={"error": "user_query is required"}
            )

        # Search knowledge base
        documents = search_knowledge_base(
            query=user_query,
            collection_name=collection_name,
            num_results=num_sources,
            min_similarity=min_similarity
        )

        # Generate answer
        if documents:
            answer_text = generate_answer(user_query, documents)
        else:
            answer_text = (
                "I couldn't find relevant information in the knowledge base to answer your question. "
                "Please provide more details or contact support directly."
            )

        # Format response
        response_data = {
            "response": answer_text,
            "sources": [
                {
                    "id": doc["id"],
                    "content": doc["content"][:500],  # Truncate for response
                    "similarity": doc["similarity"],
                    "metadata": doc["metadata"]
                }
                for doc in documents
            ],
            "metadata": {
                "num_sources": len(documents),
                "collection": collection_name,
                "min_similarity": min_similarity,
                "query_length": len(user_query)
            }
        }

        logger.info("Returning answer", source_count=len(documents))
        return JSONResponse(content=response_data)

    except Exception as e:
        logger.error("Error processing RAG query", error=str(e))
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "response": "An error occurred processing your query. Please try again."
            }
        )


if __name__ == "__main__":
    logger.info(
        "Starting RAG API Service",
        url="http://0.0.0.0:8080",
        chroma_host=CHROMA_HOST,
        chroma_port=CHROMA_PORT,
        llm_model=LLM_MODEL,
        embedding_model=EMBEDDING_MODEL,
    )
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
