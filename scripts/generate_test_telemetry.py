#!/usr/bin/env python3
"""
Test script to generate telemetry data for the RAG service.
This script sends a series of requests to the RAG service to generate telemetry data.
"""

import asyncio
import aiohttp
import random
import time
import json
import argparse
from datetime import datetime

# Default settings
DEFAULT_HOST = "http://localhost:8000"
DEFAULT_API_KEY = "dev_api_key"
DEFAULT_DURATION = 60  # seconds
DEFAULT_REQUESTS_PER_SECOND = 2

# Sample queries
SAMPLE_QUERIES = [
    "What are the key components of a RAG system?",
    "How does vector search work in RAG?",
    "Explain the difference between semantic search and keyword search",
    "What are the best practices for prompt engineering?",
    "How can I optimize my RAG system for better performance?",
    "What is the role of context in a RAG system?",
    "How do I handle long documents in RAG?",
    "What are the limitations of current RAG systems?",
    "How can I evaluate the performance of my RAG system?",
    "What are some common failure modes in RAG systems?",
]

# Sample filters
SAMPLE_FILTERS = [
    {},
    {"category": "technical"},
    {"category": "business"},
    {"date_range": {"start": "2023-01-01", "end": "2023-12-31"}},
    {"source": "documentation"},
]

# Sample retrieval strategies
SAMPLE_STRATEGIES = [
    "semantic",
    "keyword",
    "hybrid",
    "ensemble",
]

async def send_query(session, host, api_key, query_id):
    """Send a query to the RAG service."""
    query = random.choice(SAMPLE_QUERIES)
    filters = random.choice(SAMPLE_FILTERS)
    strategy = random.choice(SAMPLE_STRATEGIES)
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Request-ID": f"test-{query_id}"
    }
    
    payload = {
        "query": query,
        "filters": filters,
        "retrieval_strategy": strategy,
        "max_results": random.randint(3, 10),
        "similarity_threshold": random.uniform(0.7, 0.95),
    }
    
    start_time = time.time()
    try:
        async with session.post(f"{host}/api/v1/query", json=payload, headers=headers) as response:
            elapsed = time.time() - start_time
            status = response.status
            if status == 200:
                result = await response.json()
                print(f"Query {query_id}: Success ({elapsed:.2f}s) - Strategy: {strategy}")
                return True
            else:
                print(f"Query {query_id}: Failed with status {status} ({elapsed:.2f}s)")
                return False
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"Query {query_id}: Exception: {str(e)} ({elapsed:.2f}s)")
        return False

async def send_streaming_query(session, host, api_key, query_id):
    """Send a streaming query to the RAG service."""
    query = random.choice(SAMPLE_QUERIES)
    filters = random.choice(SAMPLE_FILTERS)
    strategy = random.choice(SAMPLE_STRATEGIES)
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Request-ID": f"test-stream-{query_id}"
    }
    
    payload = {
        "query": query,
        "filters": filters,
        "retrieval_strategy": strategy,
        "max_results": random.randint(3, 10),
        "similarity_threshold": random.uniform(0.7, 0.95),
        "stream": True,
    }
    
    start_time = time.time()
    try:
        async with session.post(f"{host}/api/v1/query/stream", json=payload, headers=headers) as response:
            elapsed = time.time() - start_time
            status = response.status
            if status == 200:
                # Read streaming response
                async for line in response.content:
                    if line:
                        pass  # Process streaming content if needed
                print(f"Streaming Query {query_id}: Success ({elapsed:.2f}s) - Strategy: {strategy}")
                return True
            else:
                print(f"Streaming Query {query_id}: Failed with status {status} ({elapsed:.2f}s)")
                return False
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"Streaming Query {query_id}: Exception: {str(e)} ({elapsed:.2f}s)")
        return False

async def check_health(session, host):
    """Check the health of the RAG service."""
    try:
        async with session.get(f"{host}/health") as response:
            if response.status == 200:
                result = await response.json()
                return result.get("status") == "ok"
            return False
    except Exception:
        return False

async def main():
    parser = argparse.ArgumentParser(description="Generate test telemetry data for the RAG service")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"RAG service host (default: {DEFAULT_HOST})")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key for authentication")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION, help=f"Test duration in seconds (default: {DEFAULT_DURATION})")
    parser.add_argument("--rate", type=float, default=DEFAULT_REQUESTS_PER_SECOND, help=f"Requests per second (default: {DEFAULT_REQUESTS_PER_SECOND})")
    args = parser.parse_args()
    
    print(f"Starting telemetry data generation for {args.duration} seconds at {args.rate} requests/second")
    print(f"Target: {args.host}")
    
    async with aiohttp.ClientSession() as session:
        # Check if the service is available
        is_healthy = await check_health(session, args.host)
        if not is_healthy:
            print(f"RAG service at {args.host} is not available. Please make sure it's running.")
            return
        
        print("RAG service is healthy. Starting test...")
        
        start_time = time.time()
        end_time = start_time + args.duration
        query_id = 0
        
        while time.time() < end_time:
            batch_start = time.time()
            tasks = []
            
            # Determine how many requests to send in this batch
            batch_size = max(1, int(args.rate))
            
            for _ in range(batch_size):
                query_id += 1
                # Randomly choose between regular and streaming queries
                if random.random() < 0.3:  # 30% streaming queries
                    task = send_streaming_query(session, args.host, args.api_key, query_id)
                else:
                    task = send_query(session, args.host, args.api_key, query_id)
                tasks.append(task)
            
            # Wait for all tasks to complete
            await asyncio.gather(*tasks)
            
            # Calculate sleep time to maintain the desired rate
            elapsed = time.time() - batch_start
            sleep_time = max(0, 1.0 / args.rate - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
        
        total_elapsed = time.time() - start_time
        print(f"Test completed. Sent {query_id} queries in {total_elapsed:.2f} seconds.")

if __name__ == "__main__":
    asyncio.run(main()) 