"""API endpoints module for handling HTTP requests."""

from typing import List, Dict, Any, Optional
import logging
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from rag_service.core.retrieval import RetrievalManager, RetrievalResult
from rag_service.core.generation import GenerationManager, GenerationResult
from rag_service.core.optimization import OptimizationManager
from rag_service.security.security_manager import SecurityManager, SecurityContext
from rag_service.monitoring.metrics import MetricsManager

# Request/Response Models
class QueryRequest(BaseModel):
    """Model for query requests."""
    query: str
    filters: Optional[Dict[str, Any]] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    model_name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class QueryResponse(BaseModel):
    """Model for query responses."""
    response: str
    context: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    timing: Dict[str, float]

class OptimizationRequest(BaseModel):
    """Model for optimization requests."""
    strategy: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)

class HealthResponse(BaseModel):
    """Model for health check responses."""
    status: str
    components: Dict[str, Any]
    timestamp: datetime

# Initialize FastAPI app
app = FastAPI(
    title="RAG Service API",
    description="Production-ready RAG service with comprehensive features",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize metrics
request_counter = Counter(
    'rag_api_requests_total',
    'Total number of API requests',
    ['endpoint', 'method', 'status']
)
request_latency = Histogram(
    'rag_api_request_duration_seconds',
    'Duration of API requests',
    ['endpoint', 'method'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
)

# Initialize tracer
tracer = trace.get_tracer(__name__)

class APIManager:
    """Manager for API endpoints and request handling."""

    def __init__(
        self,
        retrieval_manager: RetrievalManager,
        generation_manager: GenerationManager,
        optimization_manager: OptimizationManager,
        security_manager: SecurityManager,
        metrics_manager: MetricsManager,
        config: Dict[str, Any]
    ):
        """Initialize API manager.
        
        Args:
            retrieval_manager: Retrieval manager instance
            generation_manager: Generation manager instance
            optimization_manager: Optimization manager instance
            security_manager: Security manager instance
            metrics_manager: Metrics manager instance
            config: API configuration
        """
        self.retrieval_manager = retrieval_manager
        self.generation_manager = generation_manager
        self.optimization_manager = optimization_manager
        self.security_manager = security_manager
        self.metrics_manager = metrics_manager
        self.config = config

    async def authenticate_request(
        self,
        request: Request
    ) -> SecurityContext:
        """Authenticate incoming request.
        
        Args:
            request: FastAPI request
            
        Returns:
            Security context
            
        Raises:
            HTTPException: If authentication fails
        """
        try:
            # Get authentication token
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                raise HTTPException(
                    status_code=401,
                    detail="Missing authentication token"
                )

            # Create security context
            context = await self.security_manager.create_security_context(
                auth_header
            )

            # Check rate limit
            if not await self.security_manager.check_rate_limit(context):
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded"
                )

            return context

        except HTTPException:
            raise
        except Exception as e:
            logging.error("Authentication failed: %s", str(e))
            raise HTTPException(
                status_code=500,
                detail="Authentication failed"
            )

    async def process_query(
        self,
        request: QueryRequest,
        security_context: SecurityContext,
        background_tasks: BackgroundTasks
    ) -> QueryResponse:
        """Process query request.
        
        Args:
            request: Query request
            security_context: Security context
            background_tasks: Background tasks
            
        Returns:
            Query response
            
        Raises:
            HTTPException: If query processing fails
        """
        start_time = time.time()
        
        with tracer.start_span("process_query") as span:
            try:
                # Validate query
                if not await self.security_manager.validate_query(
                    request.query,
                    security_context
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid query"
                    )

                # Track timing
                timing = {}

                # Retrieve relevant documents
                retrieval_start = time.time()
                context_results = await self.retrieval_manager.retrieve(
                    query=request.query,
                    filters=request.filters
                )
                timing["retrieval"] = time.time() - retrieval_start

                # Generate response
                generation_start = time.time()
                generation_result = await self.generation_manager.generate_response(
                    query=request.query,
                    context_results=context_results,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    model_name=request.model_name
                )
                timing["generation"] = time.time() - generation_start

                # Prepare response
                response = QueryResponse(
                    response=generation_result.response,
                    context=[
                        {
                            "content": result.document.content,
                            "metadata": result.document.metadata,
                            "score": result.score
                        }
                        for result in context_results
                    ],
                    metadata={
                        "model": generation_result.model_name,
                        "token_usage": generation_result.token_usage,
                        **request.metadata
                    },
                    timing=timing
                )

                # Record metrics
                total_time = time.time() - start_time
                request_counter.labels(
                    endpoint="/query",
                    method="POST",
                    status="success"
                ).inc()
                request_latency.labels(
                    endpoint="/query",
                    method="POST"
                ).observe(total_time)

                # Add background tasks
                background_tasks.add_task(
                    self.metrics_manager.record_query_metrics,
                    request,
                    response,
                    timing,
                    security_context
                )

                span.set_attribute("query_length", len(request.query))
                span.set_attribute("context_count", len(context_results))
                span.set_status(Status(StatusCode.OK))

                return response

            except HTTPException:
                raise
            except Exception as e:
                request_counter.labels(
                    endpoint="/query",
                    method="POST",
                    status="error"
                ).inc()

                span.set_status(Status(StatusCode.ERROR))
                logging.error("Query processing failed: %s", str(e))
                raise HTTPException(
                    status_code=500,
                    detail="Query processing failed"
                )

    async def run_optimization(
        self,
        request: OptimizationRequest,
        security_context: SecurityContext
    ) -> Dict[str, Any]:
        """Run optimization process.
        
        Args:
            request: Optimization request
            security_context: Security context
            
        Returns:
            Optimization results
            
        Raises:
            HTTPException: If optimization fails
        """
        start_time = time.time()
        
        with tracer.start_span("run_optimization") as span:
            try:
                # Check permissions
                if "admin" not in security_context.roles:
                    raise HTTPException(
                        status_code=403,
                        detail="Insufficient permissions"
                    )

                # Run optimization
                result = await self.optimization_manager.optimize(
                    strategy_name=request.strategy
                )

                # Record metrics
                total_time = time.time() - start_time
                request_counter.labels(
                    endpoint="/optimize",
                    method="POST",
                    status="success"
                ).inc()
                request_latency.labels(
                    endpoint="/optimize",
                    method="POST"
                ).observe(total_time)

                span.set_attribute("strategy", request.strategy or "all")
                span.set_status(Status(StatusCode.OK))

                return result.dict()

            except HTTPException:
                raise
            except Exception as e:
                request_counter.labels(
                    endpoint="/optimize",
                    method="POST",
                    status="error"
                ).inc()

                span.set_status(Status(StatusCode.ERROR))
                logging.error("Optimization failed: %s", str(e))
                raise HTTPException(
                    status_code=500,
                    detail="Optimization failed"
                )

    async def check_health(self) -> HealthResponse:
        """Check system health.
        
        Returns:
            Health check response
            
        Raises:
            HTTPException: If health check fails
        """
        start_time = time.time()
        
        with tracer.start_span("health_check") as span:
            try:
                # Collect component health
                components = {}

                # Check retrieval
                components["retrieval"] = await self.retrieval_manager.health_check()

                # Check generation
                components["generation"] = await self.generation_manager.health_check()

                # Check optimization
                components["optimization"] = await self.optimization_manager.health_check()

                # Check security
                components["security"] = await self.security_manager.health_check()

                # Determine overall status
                status = "healthy"
                for component in components.values():
                    if component["status"] != "healthy":
                        status = "degraded"
                        break

                # Create response
                response = HealthResponse(
                    status=status,
                    components=components,
                    timestamp=datetime.now()
                )

                # Record metrics
                total_time = time.time() - start_time
                request_counter.labels(
                    endpoint="/health",
                    method="GET",
                    status="success"
                ).inc()
                request_latency.labels(
                    endpoint="/health",
                    method="GET"
                ).observe(total_time)

                span.set_attribute("status", status)
                span.set_status(Status(StatusCode.OK))

                return response

            except Exception as e:
                request_counter.labels(
                    endpoint="/health",
                    method="GET",
                    status="error"
                ).inc()

                span.set_status(Status(StatusCode.ERROR))
                logging.error("Health check failed: %s", str(e))
                raise HTTPException(
                    status_code=500,
                    detail="Health check failed"
                )

# Initialize API manager instance
api_manager = None

def get_api_manager() -> APIManager:
    """Get API manager instance."""
    if not api_manager:
        raise RuntimeError("API manager not initialized")
    return api_manager

# API routes
@app.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    background_tasks: BackgroundTasks,
    api_request: Request,
    api_manager: APIManager = Depends(get_api_manager)
):
    """Handle query requests."""
    security_context = await api_manager.authenticate_request(api_request)
    return await api_manager.process_query(
        request,
        security_context,
        background_tasks
    )

@app.post("/optimize")
async def optimize(
    request: OptimizationRequest,
    api_request: Request,
    api_manager: APIManager = Depends(get_api_manager)
):
    """Handle optimization requests."""
    security_context = await api_manager.authenticate_request(api_request)
    return await api_manager.run_optimization(request, security_context)

@app.get("/health", response_model=HealthResponse)
async def health(
    api_manager: APIManager = Depends(get_api_manager)
):
    """Handle health check requests."""
    return await api_manager.check_health()

# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions."""
    request_counter.labels(
        endpoint=request.url.path,
        method=request.method,
        status="error"
    ).inc()
    
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions."""
    request_counter.labels(
        endpoint=request.url.path,
        method=request.method,
        status="error"
    ).inc()
    
    logging.error("Unhandled exception: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "status_code": 500
        }
    ) 