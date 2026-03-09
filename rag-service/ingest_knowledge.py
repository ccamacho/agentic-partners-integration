#!/usr/bin/env python3
"""
Knowledge Base Ingestion Script.

Loads support tickets and documentation into ChromaDB for RAG.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import chromadb
import numpy as np
import structlog
from chromadb.config import Settings
from google import genai

logger = structlog.get_logger()

# Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")


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
            # Convert to numpy array (ChromaDB expects numpy arrays)
            embeddings.append(np.array(response.embeddings[0].values))
        return embeddings

    def embed_query(self, input: List[str]) -> List[np.ndarray]:
        """Generate embeddings for query texts (used when searching)."""
        return self.__call__(input)


def load_from_json_files(data_dir: str = "data") -> Dict[str, List[Dict[str, Any]]]:
    """
    Load support ticket data from JSON files.

    Args:
        data_dir: Directory containing JSON data files

    Returns:
        Dictionary with collections and their documents
    """
    # In container, script is at /app/ingest_knowledge.py and data is at /app/data
    script_dir = Path(__file__).parent  # /app
    data_path = script_dir / data_dir  # /app/data

    collections = {}

    # Load software support tickets
    software_file = data_path / "software_support_tickets.json"
    if software_file.exists():
        logger.info("Loading software support tickets", path=str(software_file))
        with open(software_file, 'r') as f:
            software_tickets = json.load(f)
            collections["software_support"] = software_tickets
            logger.info("Loaded software support documents", count=len(software_tickets))

    # Load network support tickets
    network_file = data_path / "network_support_tickets.json"
    if network_file.exists():
        logger.info("Loading network support tickets", path=str(network_file))
        with open(network_file, 'r') as f:
            network_tickets = json.load(f)
            collections["network_support"] = network_tickets
            logger.info("Loaded network support documents", count=len(network_tickets))

    # Also create combined collection for general search
    all_tickets = []
    if "software_support" in collections:
        all_tickets.extend(collections["software_support"])
    if "network_support" in collections:
        all_tickets.extend(collections["network_support"])

    if all_tickets:
        collections["support_tickets"] = all_tickets
        logger.info("Created combined collection", total=len(all_tickets))

    return collections


def ingest_collection(
    collection_name: str,
    documents: List[Dict[str, Any]],
    chroma_client,
    embedding_function
):
    """
    Ingest documents into a ChromaDB collection.

    Args:
        collection_name: Name of the collection
        documents: List of documents to ingest
        chroma_client: ChromaDB client
        embedding_function: Embedding function to use
    """
    try:
        logger.info("Creating/updating collection", collection=collection_name)

        # Get or create collection
        collection = chroma_client.get_or_create_collection(
            name=collection_name,
            embedding_function=embedding_function,
            metadata={"hnsw:space": "cosine"}
        )

        # Clear existing data (for fresh ingestion)
        existing_count = collection.count()
        if existing_count > 0:
            logger.info("Clearing existing documents", count=existing_count)
            # Delete collection and recreate
            chroma_client.delete_collection(collection_name)
            collection = chroma_client.create_collection(
                name=collection_name,
                embedding_function=embedding_function,
                metadata={"hnsw:space": "cosine"}
            )

        # Prepare data for ingestion
        ids = [doc["id"] for doc in documents]
        contents = [doc["content"] for doc in documents]
        metadatas = [doc["metadata"] for doc in documents]

        # Add documents to collection
        logger.info("Adding documents", collection=collection_name, count=len(documents))
        collection.add(
            ids=ids,
            documents=contents,
            metadatas=metadatas
        )

        # Verify ingestion
        final_count = collection.count()
        logger.info("Ingestion complete", collection=collection_name, document_count=final_count)

        # Show sample
        sample = collection.peek(limit=3)
        logger.info("Sample IDs", ids=sample["ids"])

        return collection

    except Exception as e:
        logger.error("Failed to ingest collection", collection=collection_name, error=str(e))
        raise


def main():
    """Main ingestion function."""
    logger.info("Starting knowledge base ingestion...")

    # Check Google API key
    if not GOOGLE_API_KEY:
        logger.error("GOOGLE_API_KEY environment variable not set")
        return

    # Initialize new Google GenAI client
    genai_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Google GenAI client initialized", model=EMBEDDING_MODEL)

    # Initialize ChromaDB client
    logger.info("Connecting to ChromaDB", host=CHROMA_HOST, port=CHROMA_PORT)
    chroma_client = chromadb.HttpClient(
        host=CHROMA_HOST,
        port=CHROMA_PORT,
        settings=Settings(anonymized_telemetry=False)
    )

    # Initialize custom embedding function using new Google GenAI SDK
    embedding_function = GoogleGenAIEmbeddingFunction(
        client=genai_client,
        model_name=EMBEDDING_MODEL
    )

    # Load data from JSON files
    logger.info("Loading knowledge base data from JSON files...")
    data = load_from_json_files()

    # Ingest each collection
    for collection_name, documents in data.items():
        ingest_collection(
            collection_name=collection_name,
            documents=documents,
            chroma_client=chroma_client,
            embedding_function=embedding_function
        )

    collections = chroma_client.list_collections()
    summary = {coll.name: coll.count() for coll in collections}
    logger.info("Knowledge base ingestion complete", collections=summary)
    logger.info("RAG service is ready to use!")


if __name__ == "__main__":
    main()
