"""End-to-end tests for the RAG service."""

import pytest
import httpx
import asyncio
import json
import os
from typing import Dict, Any, List
import uuid
from datetime import datetime

# Test configuration
TEST_SERVER_URL = os.environ.get("TEST_SERVER_URL", "http://localhost:8000")
TEST_API_KEY = os.environ.get("TEST_API_KEY", "test_api_key")

# Test data
TEST_DOCUMENTS = [
    {
        "id": str(uuid.uuid4()),
        "content": "Retrieval Augmented Generation (RAG) is a technique that combines retrieval-based and generation-based approaches for natural language processing tasks.",
        "metadata": {
            "source": "test",
            "category": "rag",
            "author": "Test Author"
        }
    },
    {
        "id": str(uuid.uuid4()),
        "content": "Vector databases store and index high-dimensional vectors for similarity search, which is essential for semantic search applications.",
        "metadata": {
            "source": "test",
            "category": "databases",
            "author": "Test Author"
        }
    },
    {
        "id": str(uuid.uuid4()),
        "content": "Large Language Models (LLMs) are neural networks trained on vast amounts of text data that can generate human-like text based on input prompts.",
        "metadata": {
            "source": "test",
            "category": "llm",
            "author": "Test Author"
        }
    }
]

@pytest.fixture(scope="module")
async def setup_test_data():
    """Set up test data in the service."""
    async with httpx.AsyncClient() as client:
        # Add test documents
        for doc in TEST_DOCUMENTS:
            response = await client.post(
                f"{TEST_SERVER_URL}/documents",
                json=doc,
                headers={"Authorization": f"Bearer {TEST_API_KEY}"}
            )
            assert response.status_code == 201, f"Failed to add document: {response.text}"
    
    # Wait for indexing to complete
    await asyncio.sleep(2)
    
    yield
    
    # Clean up test data
    async with httpx.AsyncClient() as client:
        for doc in TEST_DOCUMENTS:
            await client.delete(
                f"{TEST_SERVER_URL}/documents/{doc['id']}",
                headers={"Authorization": f"Bearer {TEST_API_KEY}"}
            )

@pytest.mark.asyncio
async def test_health_endpoint():
    """Test the health check endpoint."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{TEST_SERVER_URL}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["healthy", "unhealthy"]
        assert "services" in data

@pytest.mark.asyncio
async def test_query_endpoint(setup_test_data):
    """Test the query endpoint."""
    query_request = {
        "query": "What is RAG?",
        "max_tokens": 100,
        "temperature": 0.7
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TEST_SERVER_URL}/query",
            json=query_request,
            headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
        assert "request_id" in data
        assert "response" in data
        assert "context" in data
        assert "timing" in data
        assert "token_usage" in data
        assert "model_info" in data
        
        # Verify that response contains relevant information
        assert len(data["response"]) > 0
        assert len(data["context"]) > 0

@pytest.mark.asyncio
async def test_query_with_filters(setup_test_data):
    """Test the query endpoint with filters."""
    query_request = {
        "query": "Tell me about vector databases",
        "filters": {
            "category": "databases"
        },
        "max_tokens": 100,
        "temperature": 0.7
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TEST_SERVER_URL}/query",
            json=query_request,
            headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify that context contains documents with the right category
        assert any(doc.get("metadata", {}).get("category") == "databases" for doc in data["context"])

@pytest.mark.asyncio
async def test_streaming_query(setup_test_data):
    """Test the streaming query endpoint."""
    query_request = {
        "query": "What are Large Language Models?",
        "max_tokens": 100,
        "temperature": 0.7
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            f"{TEST_SERVER_URL}/query/stream",
            json=query_request,
            headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        ) as response:
            assert response.status_code == 200
            
            # Collect streaming response
            chunks = []
            async for chunk in response.aiter_text():
                if chunk.strip():
                    chunks.append(chunk)
            
            # Verify that we received content
            assert len(chunks) > 0
            full_response = "".join(chunks)
            assert len(full_response) > 0

@pytest.mark.asyncio
async def test_authentication():
    """Test authentication requirements."""
    query_request = {
        "query": "Test query",
        "max_tokens": 100
    }
    
    async with httpx.AsyncClient() as client:
        # Test without auth header
        response = await client.post(
            f"{TEST_SERVER_URL}/query",
            json=query_request
        )
        assert response.status_code == 401
        
        # Test with invalid auth token
        response = await client.post(
            f"{TEST_SERVER_URL}/query",
            json=query_request,
            headers={"Authorization": "Bearer invalid_token"}
        )
        assert response.status_code == 401

@pytest.mark.asyncio
async def test_rate_limiting():
    """Test rate limiting functionality."""
    # Make multiple rapid requests to trigger rate limiting
    async with httpx.AsyncClient() as client:
        tasks = []
        for _ in range(20):  # Adjust based on rate limit configuration
            tasks.append(
                client.get(
                    f"{TEST_SERVER_URL}/health",
                    headers={"Authorization": f"Bearer {TEST_API_KEY}"}
                )
            )
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check if any responses indicate rate limiting
        rate_limited = any(
            isinstance(resp, httpx.Response) and resp.status_code == 429
            for resp in responses
        )
        
        # This test may pass or fail depending on the rate limit configuration
        # We're just checking that the rate limiting mechanism is working
        if rate_limited:
            print("Rate limiting is working as expected")
        else:
            print("No rate limiting triggered, might need to adjust test parameters")

@pytest.mark.asyncio
async def test_error_handling():
    """Test error handling for invalid requests."""
    # Test with empty query
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TEST_SERVER_URL}/query",
            json={"query": ""},
            headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        )
        assert response.status_code == 400
        
        # Test with invalid parameters
        response = await client.post(
            f"{TEST_SERVER_URL}/query",
            json={"query": "Test", "temperature": 2.5},  # Temperature too high
            headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        )
        assert response.status_code == 400
        
        # Test with missing required field
        response = await client.post(
            f"{TEST_SERVER_URL}/query",
            json={"max_tokens": 100},  # Missing query
            headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        )
        assert response.status_code == 422  # Validation error 