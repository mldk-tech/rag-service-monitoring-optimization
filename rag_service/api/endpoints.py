"""API endpoints module for handling HTTP requests."""

from typing import List, Dict, Any, Optional, Union
import logging
import time
from datetime import datetime
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator, constr
from prometheus_client import Counter, Histogram, Gauge
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import structlog
from functools import wraps
import asyncio
from contextlib import asynccontextmanager

from rag_service.core.retrieval import RetrievalManager, RetrievalResult
from rag_service.core.generation import GenerationManager, GenerationResult
from rag_service.core.optimization import OptimizationManager
from rag_service.security.security_manager import SecurityManager, SecurityContext
from rag_service.monitoring.metrics import MetricsManager

# Enhanced logging setup
logger = structlog.get_logger(__name__)

# Request/Response Models with enhanced validation
class QueryRequest(BaseModel):
    """Enhanced model for query requests with validation."""
    query: constr(min_length=1, max_length=1000) = Field(
        ...,
        description="Query string between 1 and 1000 characters"
    )
    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional filters for document retrieval"
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=2048,
        description="Maximum number of tokens to generate"
    )
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Generation temperature between 0 and 2"
    )
    model_name: Optional[str] = Field(
        default=None,
        description="Name of the model to use"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata for the request"
    )
    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique request identifier"
    )

    @validator("filters")
    def validate_filters(cls, v):
        """Validate filters structure."""
        if v is not None:
            if not isinstance(v, dict):
                raise ValueError("Filters must be a dictionary")
            if len(v) > 10:  # Limit number of filters
                raise ValueError("Too many filters (max 10)")
        return v

    @validator("metadata")
    def validate_metadata(cls, v):
        """Validate metadata structure and size."""
        if len(v) > 20:  # Limit metadata fields
            raise ValueError("Too many metadata fields (max 20)")
        return v

class QueryResponse(BaseModel):
    """Enhanced model for query responses."""
    request_id: str
    response: str
    context: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    timing: Dict[str, float]
    token_usage: Dict[str, int]
    model_info: Dict[str, Any]
    created_at: datetime = Field(default_factory=datetime.utcnow)

class OptimizationRequest(BaseModel):
    """Enhanced model for optimization requests."""
    strategy: str = Field(
        ...,
        description="Optimization strategy to apply"
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific parameters"
    )
    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique request identifier"
    )

    @validator("strategy")
    def validate_strategy(cls, v):
        """Validate optimization strategy."""
        valid_strategies = {"auto", "cost", "latency", "quality"}
        if v not in valid_strategies:
            raise ValueError(f"Invalid strategy. Must be one of: {valid_strategies}")
        return v

class ErrorResponse(BaseModel):
    """Enhanced error response model."""
    error: str
    detail: str
    request_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    code: str
    path: str
    method: str

class HealthResponse(BaseModel):
    """Enhanced health check response model."""
    status: str
    components: Dict[str, Dict[str, Any]]
    metrics: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: str
    uptime: float

# Initialize FastAPI app with enhanced configuration
app = FastAPI(
    title="RAG Service API",
    description="Production-ready RAG service with comprehensive features",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# Enhanced CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)

# Enhanced metrics
request_counter = Counter(
    'rag_api_requests_total',
    'Total number of API requests',
    ['endpoint', 'method', 'status', 'error_type']
)
request_latency = Histogram(
    'rag_api_request_duration_seconds',
    'Duration of API requests',
    ['endpoint', 'method', 'status'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)
active_requests = Gauge(
    'rag_api_active_requests',
    'Number of currently active requests',
    ['endpoint']
)

# Initialize tracer
tracer = trace.get_tracer(__name__)

# Security scheme
security = HTTPBearer()

class APIManager:
    """Enhanced manager for API endpoints and request handling."""

    def __init__(
        self,
        retrieval_manager: RetrievalManager,
        generation_manager: GenerationManager,
        optimization_manager: OptimizationManager,
        security_manager: SecurityManager,
        metrics_manager: MetricsManager,
        config: Dict[str, Any]
    ):
        """Initialize API manager with enhanced configuration.
        
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
        self.start_time = time.time()

        # Initialize rate limiters
        self.rate_limiters = {}
        
        logger.info(
            "api_manager_initialized",
            config=config
        )

    async def authenticate_request(
        self,
        request: Request,
        credentials: HTTPAuthorizationCredentials = Depends(security)
    ) -> SecurityContext:
        """Authenticate incoming request with enhanced security.
        
        Args:
            request: FastAPI request
            credentials: Security credentials
            
        Returns:
            Security context
            
        Raises:
            HTTPException: If authentication fails
        """
        request_id = request.state.request_id
        
        with tracer.start_span("authenticate_request") as span:
            try:
                span.set_attribute("request_id", request_id)
                
                if not credentials:
                    logger.warning(
                        "missing_auth_token",
                        request_id=request_id
                    )
                    raise HTTPException(
                        status_code=401,
                        detail="Missing authentication token"
                    )

                # Create security context
                context = await self.security_manager.create_security_context(
                    credentials.credentials
                )
                
                # Check rate limit
                await self._check_rate_limit(context, request)

                span.set_status(Status(StatusCode.OK))
                return context

            except HTTPException as e:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error", str(e))
                raise
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error", str(e))
                logger.error(
                    "authentication_failed",
                    request_id=request_id,
                    error=str(e),
                    error_type=type(e).__name__
                )
                raise HTTPException(
                    status_code=500,
                    detail="Authentication failed"
                )

    async def _check_rate_limit(
        self,
        context: SecurityContext,
        request: Request
    ) -> None:
        """Check rate limits for the request.
        
        Args:
            context: Security context
            request: FastAPI request
            
        Raises:
            HTTPException: If rate limit is exceeded
        """
        # Get rate limit key
        key = f"{context.user_id}:{request.url.path}"
        
        # Check rate limit
        if not await self.security_manager.check_rate_limit(
            context,
            key=key
        ):
            logger.warning(
                "rate_limit_exceeded",
                user_id=context.user_id,
                path=request.url.path
            )
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded"
            )

    async def process_query(
        self,
        request: QueryRequest,
        security_context: SecurityContext,
        background_tasks: BackgroundTasks,
        http_request: Request
    ) -> QueryResponse:
        """Process query request with enhanced monitoring and error handling.
        
        Args:
            request: Query request
            security_context: Security context
            background_tasks: Background tasks
            http_request: HTTP request
            
        Returns:
            Query response
            
        Raises:
            HTTPException: If query processing fails
        """
        request_id = request.request_id
        start_time = time.time()
        
        # Track active requests
        active_requests.labels(endpoint="/query").inc()
        
        with tracer.start_span("process_query") as span:
            try:
                span.set_attribute("request_id", request_id)
                span.set_attribute("query", request.query)
                
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
                try:
                    context_results = await self.retrieval_manager.retrieve(
                        query=request.query,
                        filters=request.filters
                    )
                    timing["retrieval"] = time.time() - retrieval_start
                except Exception as e:
                    logger.error(
                        "retrieval_failed",
                        request_id=request_id,
                        error=str(e)
                    )
                    raise HTTPException(
                        status_code=500,
                        detail="Document retrieval failed"
                    )

                # Generate response
                generation_start = time.time()
                try:
                    generation_result = await self.generation_manager.generate_response(
                        query=request.query,
                        context_results=context_results,
                        max_tokens=request.max_tokens,
                        temperature=request.temperature,
                        model_name=request.model_name
                    )
                    timing["generation"] = time.time() - generation_start
                except Exception as e:
                    logger.error(
                        "generation_failed",
                        request_id=request_id,
                        error=str(e)
                    )
                    raise HTTPException(
                        status_code=500,
                        detail="Response generation failed"
                    )

                # Prepare response
                response = QueryResponse(
                    request_id=request_id,
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
                        "user_id": security_context.user_id,
                        "ip_address": http_request.client.host,
                        **request.metadata
                    },
                    timing=timing,
                    token_usage=generation_result.token_usage,
                    model_info={
                        "name": generation_result.model_name,
                        "parameters": {
                            "temperature": request.temperature,
                            "max_tokens": request.max_tokens
                        }
                    }
                )

                # Record metrics
                total_time = time.time() - start_time
                request_counter.labels(
                    endpoint="/query",
                    method="POST",
                    status="success",
                    error_type=""
                ).inc()
                request_latency.labels(
                    endpoint="/query",
                    method="POST",
                    status="success"
                ).observe(total_time)

                # Add background tasks
                background_tasks.add_task(
                    self.metrics_manager.record_query_metrics,
                    request,
                    response,
                    timing,
                    security_context
                )

                span.set_status(Status(StatusCode.OK))
                return response

            except HTTPException:
                raise
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error", str(e))
                logger.error(
                    "query_processing_failed",
                    request_id=request_id,
                    error=str(e),
                    error_type=type(e).__name__
                )
                request_counter.labels(
                    endpoint="/query",
                    method="POST",
                    status="error",
                    error_type=type(e).__name__
                ).inc()
                raise HTTPException(
                    status_code=500,
                    detail="Query processing failed"
                )
                
            finally:
                active_requests.labels(endpoint="/query").dec()

    async def optimize_system(
        self,
        request: OptimizationRequest,
        security_context: SecurityContext,
        background_tasks: BackgroundTasks
    ) -> Dict[str, Any]:
        """Optimize system with enhanced monitoring.
        
        Args:
            request: Optimization request
            security_context: Security context
            background_tasks: Background tasks
            
        Returns:
            Optimization results
            
        Raises:
            HTTPException: If optimization fails
        """
        request_id = request.request_id
        start_time = time.time()
        
        with tracer.start_span("optimize_system") as span:
            try:
                span.set_attribute("request_id", request_id)
                span.set_attribute("strategy", request.strategy)
                
                # Check permissions
                if not await self.security_manager.check_permission(
                    security_context,
                    "optimize_system"
                ):
                    raise HTTPException(
                        status_code=403,
                        detail="Insufficient permissions"
                    )

                # Run optimization
                try:
                    results = await self.optimization_manager.optimize(
                        strategy=request.strategy,
                        params=request.params
                    )
                except ValueError as e:
                    raise HTTPException(
                        status_code=400,
                        detail=str(e)
                    )
                except Exception as e:
                    logger.error(
                        "optimization_failed",
                        request_id=request_id,
                        error=str(e)
                    )
                    raise HTTPException(
                        status_code=500,
                        detail="Optimization failed"
                    )

                # Record metrics
                total_time = time.time() - start_time
                request_counter.labels(
                    endpoint="/optimize",
                    method="POST",
                    status="success",
                    error_type=""
                ).inc()
                request_latency.labels(
                    endpoint="/optimize",
                    method="POST",
                    status="success"
                ).observe(total_time)

                span.set_status(Status(StatusCode.OK))
                return {
                    "request_id": request_id,
                    "results": results,
                    "timing": {
                        "total": total_time
                    }
                }

            except HTTPException:
                raise
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error", str(e))
                logger.error(
                    "optimization_failed",
                    request_id=request_id,
                    error=str(e),
                    error_type=type(e).__name__
                )
                request_counter.labels(
                    endpoint="/optimize",
                    method="POST",
                    status="error",
                    error_type=type(e).__name__
                ).inc()
                raise HTTPException(
                    status_code=500,
                    detail="Optimization failed"
                )

    async def check_health(self) -> HealthResponse:
        """Enhanced health check with comprehensive status."""
        start_time = time.time()
        
        with tracer.start_span("health_check") as span:
            try:
                # Check component health
                components = {}
                
                # Check retrieval
                try:
                    retrieval_status = await self.retrieval_manager.health_check()
                    components["retrieval"] = {
                        "status": "healthy",
                        "details": retrieval_status
                    }
                except Exception as e:
                    components["retrieval"] = {
                        "status": "unhealthy",
                        "error": str(e)
                    }

                # Check generation
                try:
                    generation_status = await self.generation_manager.health_check()
                    components["generation"] = {
                        "status": "healthy",
                        "details": generation_status
                    }
                except Exception as e:
                    components["generation"] = {
                        "status": "unhealthy",
                        "error": str(e)
                    }

                # Check optimization
                try:
                    optimization_status = await self.optimization_manager.health_check()
                    components["optimization"] = {
                        "status": "healthy",
                        "details": optimization_status
                    }
                except Exception as e:
                    components["optimization"] = {
                        "status": "unhealthy",
                        "error": str(e)
                    }

                # Get metrics
                metrics = {
                    "requests": {
                        "total": request_counter._value.sum(),
                        "active": active_requests._value.sum()
                    },
                    "latency": {
                        "avg": request_latency._sum.sum() / max(request_latency._count.sum(), 1)
                    }
                }

                # Determine overall status
                status = "healthy"
                if any(c["status"] == "unhealthy" for c in components.values()):
                    status = "degraded"
                if all(c["status"] == "unhealthy" for c in components.values()):
                    status = "unhealthy"

                response = HealthResponse(
                    status=status,
                    components=components,
                    metrics=metrics,
                    version=self.config.get("version", "1.0.0"),
                    uptime=time.time() - self.start_time
                )

                # Record metrics
                request_counter.labels(
                    endpoint="/health",
                    method="GET",
                    status="success",
                    error_type=""
                ).inc()
                request_latency.labels(
                    endpoint="/health",
                    method="GET",
                    status="success"
                ).observe(time.time() - start_time)

                span.set_status(Status(StatusCode.OK))
                return response

            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error", str(e))
                logger.error(
                    "health_check_failed",
                    error=str(e),
                    error_type=type(e).__name__
                )
                request_counter.labels(
                    endpoint="/health",
                    method="GET",
                    status="error",
                    error_type=type(e).__name__
                ).inc()
                raise HTTPException(
                    status_code=500,
                    detail="Health check failed"
                )

@app.middleware("http")
async def request_middleware(request: Request, call_next):
    """Enhanced request middleware for logging and monitoring."""
    request_id = str(uuid4())
    request.state.request_id = request_id
    
    # Start timing
    start_time = time.time()
    
    # Setup structured logging context
    log = logger.bind(
        request_id=request_id,
        path=request.url.path,
        method=request.method
    )
    
    try:
        # Log request
        log.info("request_started")
        
        # Process request
        response = await call_next(request)
        
        # Log response
        duration = time.time() - start_time
        log.info(
            "request_completed",
            status_code=response.status_code,
            duration=duration
        )
        
        # Add headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = str(duration)
        
        return response
        
    except Exception as e:
        # Log error
        log.error(
            "request_failed",
            error=str(e),
            error_type=type(e).__name__
        )
        
        # Create error response
        error_response = ErrorResponse(
            error="Internal Server Error",
            detail=str(e),
            request_id=request_id,
            code="INTERNAL_ERROR",
            path=request.url.path,
            method=request.method
        )
        
        return JSONResponse(
            status_code=500,
            content=error_response.dict()
        )

# API endpoints
@app.post("/query")
async def query(
    request: QueryRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    api_manager: APIManager = Depends(get_api_manager),
    security_context: SecurityContext = Depends(APIManager.authenticate_request)
) -> QueryResponse:
    """Enhanced query endpoint."""
    return await api_manager.process_query(
        request,
        security_context,
        background_tasks,
        http_request
    )

@app.post("/optimize")
async def optimize(
    request: OptimizationRequest,
    background_tasks: BackgroundTasks,
    api_manager: APIManager = Depends(get_api_manager),
    security_context: SecurityContext = Depends(APIManager.authenticate_request)
) -> Dict[str, Any]:
    """Enhanced optimization endpoint."""
    return await api_manager.optimize_system(
        request,
        security_context,
        background_tasks
    )

@app.get("/health")
async def health_check(
    api_manager: APIManager = Depends(get_api_manager)
) -> HealthResponse:
    """Enhanced health check endpoint."""
    return await api_manager.check_health()

# Initialize API manager instance
api_manager = None

def get_api_manager() -> APIManager:
    """Get API manager instance."""
    if not api_manager:
        raise RuntimeError("API manager not initialized")
    return api_manager 