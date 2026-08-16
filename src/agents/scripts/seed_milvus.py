                                                    
                                     
 
                                                                 
                                                                  
                                         
 
                                            
 
                                                                     
                                                                   
                                                                          
                                                                     
                                

                      
"""
Seed Milvus Vector Database with Product Catalog Embeddings

This script creates the product_catalog collection in Milvus and populates it
with product embeddings generated using VyapaarOS's NV-EmbedQA-E5-v5 model.

Features:
- Skip-if-exists: Won't re-seed if collection already has data
- Retry logic: Waits for Milvus to be ready with exponential backoff
- Flexible endpoints: Supports VyapaarOS API Catalog or local NIM

Usage (local development):
    cd src/agents
    source .venv/bin/activate
    python scripts/seed_milvus.py

Usage (Docker):
    docker compose run --rm milvus-seeder

Environment Variables:
    VYAPAAR_LLM_API_KEY      - Required for VyapaarOS API Catalog (public endpoint)
    MILVUS_URI          - Milvus connection URI (default: http://localhost:19530)
    FORCE_RESEED        - Set to "true" to force re-seeding even if data exists

    NIM Deployment Configuration (consistent with NAT agent configs):
    NIM_EMBED_BASE_URL  - Embedding API base URL
                          Public (default): https://integrate.api.nvidia.com/v1
                          Local NIM example: http://embedqa:8000/v1
    NIM_EMBED_MODEL_NAME - Embedding model name (default: nvidia/nv-embedqa-e5-v5)

    Legacy (backward compatibility):
    EMBED_API_URL       - Full embedding endpoint URL (overrides NIM_EMBED_BASE_URL)
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

                                                                
                                             
                                                     
try:
                           
    sys.path.insert(0, "/app")
    from src.data.product_catalog import PRODUCTS
except ImportError:
                                         
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))
    from src.data.product_catalog import PRODUCTS

                                
VYAPAAR_LLM_API_KEY = os.environ.get("VYAPAAR_LLM_API_KEY", "")
MILVUS_URI = os.environ.get("MILVUS_URI", "http://localhost:19530")
FORCE_RESEED = os.environ.get("FORCE_RESEED", "false").lower() == "true"

                                                                                   
                                                                
                                                                             
COLLECTION_NAME = "product_catalog"
DEFAULT_EMBED_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_EMBED_MODEL = "nvidia/nv-embedqa-e5-v5"

                                                                              
                                                               
NIM_EMBED_BASE_URL = os.environ.get("NIM_EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL)
EMBEDDING_MODEL = os.environ.get("NIM_EMBED_MODEL_NAME", DEFAULT_EMBED_MODEL)
EMBEDDING_DIM = 1024                              

                                  
                                                                            
                                              
legacy_embed_url = os.environ.get("EMBED_API_URL")
if legacy_embed_url:
    EMBED_API_URL = legacy_embed_url
else:
    EMBED_API_URL = f"{NIM_EMBED_BASE_URL}/embeddings"

                     
MAX_RETRIES = 30                                     
RETRY_DELAY_BASE = 2                                               
RETRY_DELAY_MAX = 30                                 


def wait_for_milvus() -> bool:
    """
    Wait for Milvus to be ready with exponential backoff.

    Returns:
        True if Milvus is ready, False if max retries exceeded
    """
    from pymilvus import connections
    from pymilvus.exceptions import MilvusException

                             
    uri = MILVUS_URI.replace("http://", "").replace("https://", "")
    host, port = uri.split(":")

    print(f"\nWaiting for Milvus at {MILVUS_URI}...")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            connections.connect(alias="default", host=host, port=port)
            print(f"  Connected to Milvus (attempt {attempt})")
            return True
        except MilvusException as e:
            delay = min(RETRY_DELAY_BASE * (1.5 ** (attempt - 1)), RETRY_DELAY_MAX)
            print(f"  Attempt {attempt}/{MAX_RETRIES}: Milvus not ready - {e}")
            print(f"  Retrying in {delay:.1f}s...")
            time.sleep(delay)
        except Exception as e:
            delay = min(RETRY_DELAY_BASE * (1.5 ** (attempt - 1)), RETRY_DELAY_MAX)
            print(f"  Attempt {attempt}/{MAX_RETRIES}: Connection error - {e}")
            print(f"  Retrying in {delay:.1f}s...")
            time.sleep(delay)

    print(f"\nERROR: Failed to connect to Milvus after {MAX_RETRIES} attempts")
    return False


def check_collection_exists_with_data() -> bool:
    """
    Check if the collection exists and has data.

    Returns:
        True if collection exists and has data, False otherwise
    """
    from pymilvus import Collection, utility

    if not utility.has_collection(COLLECTION_NAME):
        print(f"  Collection '{COLLECTION_NAME}' does not exist")
        return False

    collection = Collection(COLLECTION_NAME)
    collection.load()
    count = collection.num_entities

    if count > 0:
        print(f"  Collection '{COLLECTION_NAME}' exists with {count} entities")
        return True

    print(f"  Collection '{COLLECTION_NAME}' exists but is empty")
    return False


def is_using_public_endpoint() -> bool:
    """Check if using the public VyapaarOS API Catalog endpoint."""
    return NIM_EMBED_BASE_URL == DEFAULT_EMBED_BASE_URL


def wait_for_embedding_service() -> bool:
    """
    Wait for the embedding service to be ready.

    Returns:
        True if service is ready, False otherwise
    """
                                                         
    if is_using_public_endpoint():
        if not VYAPAAR_LLM_API_KEY:
            print("\nERROR: VYAPAAR_LLM_API_KEY environment variable is required")
            print("Set it with: export VYAPAAR_LLM_API_KEY=nvapi-xxx")
            return False
        print("\nUsing VyapaarOS API Catalog for embeddings")
        return True

                                             
    print(f"\nWaiting for embedding service at {EMBED_API_URL}...")

                                       
    base_url = EMBED_API_URL.rsplit("/", 1)[0]                      
    health_url = f"{base_url}/health/ready"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(health_url)
                if response.status_code == 200:
                    print(f"  Embedding service ready (attempt {attempt})")
                    return True
        except Exception:
            pass

        delay = min(RETRY_DELAY_BASE * (1.5 ** (attempt - 1)), RETRY_DELAY_MAX)
        print(f"  Attempt {attempt}/{MAX_RETRIES}: Embedding service not ready")
        print(f"  Retrying in {delay:.1f}s...")
        time.sleep(delay)

    print(f"\nERROR: Embedding service not ready after {MAX_RETRIES} attempts")
    return False


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings using VyapaarOS NIM API (hosted or local).

    Args:
        texts: List of text strings to embed

    Returns:
        List of embedding vectors
    """
    print(f"  Generating embeddings for {len(texts)} texts...")

    headers = {
        "Content-Type": "application/json",
    }

                                               
    if VYAPAAR_LLM_API_KEY:
        headers["Authorization"] = f"Bearer {VYAPAAR_LLM_API_KEY}"

    payload = {
        "input": texts,
        "model": EMBEDDING_MODEL,
        "input_type": "passage",
        "encoding_format": "float",
        "truncate": "END",
    }

    with httpx.Client(timeout=120.0) as client:
        response = client.post(EMBED_API_URL, headers=headers, json=payload)
        response.raise_for_status()

    result = response.json()
    embeddings = [item["embedding"] for item in result["data"]]

    print(f"  Generated {len(embeddings)} embeddings (dim={len(embeddings[0])})")
    return embeddings


def create_milvus_collection():
    """Create the product_catalog collection in Milvus."""
    from pymilvus import (
        Collection,
        CollectionSchema,
        DataType,
        FieldSchema,
        utility,
    )

                                        
    if utility.has_collection(COLLECTION_NAME):
        print(f"\n2. Dropping existing collection '{COLLECTION_NAME}'...")
        utility.drop_collection(COLLECTION_NAME)
        print("  Collection dropped")
    else:
        print(f"\n2. Collection '{COLLECTION_NAME}' does not exist, will create new")

                              
    print(f"\n3. Creating collection '{COLLECTION_NAME}'...")

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=100),
        FieldSchema(name="sku", dtype=DataType.VARCHAR, max_length=50),
        FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=200),
        FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(name="subcategory", dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(name="price_cents", dtype=DataType.INT64),
        FieldSchema(name="stock_count", dtype=DataType.INT64),
                                                                          
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="attributes_json", dtype=DataType.VARCHAR, max_length=1000),
                                                                           
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
    ]

    schema = CollectionSchema(
        fields=fields,
        description="Product catalog for ARAG recommendation agent",
    )

    collection = Collection(name=COLLECTION_NAME, schema=schema)
    print(f"  Collection created with {len(fields)} fields")

                                                       
                                                                 
    print("\n4. Creating vector index...")
    index_params = {
        "metric_type": "L2",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128},
    }
    collection.create_index(field_name="vector", index_params=index_params)
    print("  Vector index created (IVF_FLAT, L2)")

    return collection


def seed_products(collection: Any):
    """Generate embeddings and insert products into Milvus."""
    print("\n5. Preparing product data...")

                                                                         
    embedding_texts = []
    for product in PRODUCTS:
        attrs = ", ".join(product["attributes"])
        text = (
            f"{product['name']}. {product['description']} "
            f"Category: {product['category']}. Attributes: {attrs}"
        )
        embedding_texts.append(text)

    print(f"  Prepared {len(embedding_texts)} product texts for embedding")

                         
    print("\n6. Generating embeddings via VyapaarOS NIM API...")
    embeddings = get_embeddings(embedding_texts)

                                
    print("\n7. Inserting products into Milvus...")

    data = [
        [p["id"] for p in PRODUCTS],
        [p["sku"] for p in PRODUCTS],
        [p["name"] for p in PRODUCTS],
        [p["category"] for p in PRODUCTS],
        [p["subcategory"] for p in PRODUCTS],
        [p["price_cents"] for p in PRODUCTS],
        [p["stock_count"] for p in PRODUCTS],
                                                                                        
        [p["description"] for p in PRODUCTS],
        [json.dumps(p["attributes"]) for p in PRODUCTS],
        embeddings,
    ]

    collection.insert(data)
    print(f"  Inserted {len(PRODUCTS)} products")

                                               
    print("\n8. Loading collection into memory...")
    collection.load()
    print("  Collection loaded and ready for queries")

    return len(PRODUCTS)


def verify_collection(collection: Any):
    """Verify the collection by running a test query."""
    print("\n9. Running verification query...")

                                         
    test_query = "casual pants to wear with a t-shirt"
    query_embedding = get_embeddings([test_query])[0]

                                                  
    search_params = {"metric_type": "L2", "params": {"nprobe": 10}}

    results = collection.search(
        data=[query_embedding],
        anns_field="vector",
        param=search_params,
        limit=5,
        output_fields=["id", "name", "category", "text"],
    )

    print(f"\n  Test query: '{test_query}'")
    print("  Top 5 results:")
    for i, hit in enumerate(results[0]):
        name = hit.entity.get("name")
        category = hit.entity.get("category")
        print(f"    {i + 1}. {name} ({category}) - score: {hit.score:.4f}")

    return True


def main():
    """Main entry point."""
    print("=" * 60)
    print("MILVUS PRODUCT CATALOG SEEDER")
    print("=" * 60)
    print("\nConfiguration:")
    print(f"  Milvus URI: {MILVUS_URI}")
    print(f"  Collection: {COLLECTION_NAME}")
    print(f"  Embedding Model: {EMBEDDING_MODEL}")
    print(f"  Embedding Base URL: {NIM_EMBED_BASE_URL}")
    print(f"  Embedding Endpoint: {EMBED_API_URL}")
    print(f"  Embedding Dimension: {EMBEDDING_DIM}")
    print(f"  Using Public API: {is_using_public_endpoint()}")
    print(f"  Products to seed: {len(PRODUCTS)}")
    print(f"  Force reseed: {FORCE_RESEED}")

    try:
                                             
        print("\n1. Connecting to Milvus...")
        if not wait_for_milvus():
            sys.exit(1)

                                                          
        if not FORCE_RESEED and check_collection_exists_with_data():
            print("\n" + "=" * 60)
            print("SKIPPED: Collection already seeded with data")
            print("Set FORCE_RESEED=true to force re-seeding")
            print("=" * 60)
            sys.exit(0)

                                            
        if not wait_for_embedding_service():
            sys.exit(1)

                                   
        collection = create_milvus_collection()

                                               
        count = seed_products(collection)

                                        
        verify_collection(collection)

        print("\n" + "=" * 60)
        print(f"SUCCESS: Seeded {count} products into Milvus")
        print("=" * 60)
        print("\nThe recommendation and search agents can now retrieve products!")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
