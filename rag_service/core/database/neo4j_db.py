"""Neo4j database client implementation."""

import asyncio
import json
from typing import Dict, Any, Optional, List
import structlog
from neo4j import AsyncGraphDatabase, AsyncDriver
from neo4j.exceptions import (
    ServiceUnavailable,
    AuthError,
    ClientError,
    TransientError
)
from opentelemetry import trace
import datetime
import uuid

from rag_service.core.database.base import (
    DatabaseClient,
    Document,
    ConnectionState,
    DatabaseError,
    ConnectionError,
    QueryError,
    QueryTimeout
)

# Get a logger
logger = structlog.get_logger(__name__)

# Get a tracer
tracer = trace.get_tracer(__name__)

class Neo4jClient(DatabaseClient):
    """Client for Neo4j graph database operations."""
    
    def __init__(self, config: Dict[str, Any], metrics_manager):
        """Initialize the Neo4j client with configuration."""
        self.config = config
        self.metrics = metrics_manager
        self.driver = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.database = config.get("database", "neo4j")
        
    async def connect(self):
        """Connect to Neo4j."""
        if self.connection_state == ConnectionState.CONNECTED:
            return
            
        self.connection_state = ConnectionState.CONNECTING
        logger.info(
            "connecting_to_neo4j", 
            uri=self.config.get("uri", "neo4j://localhost:7687"),
            database=self.database
        )
        
        try:
            # Create Neo4j driver
            self.driver = AsyncGraphDatabase.driver(
                self.config.get("uri", "neo4j://localhost:7687"),
                auth=(
                    self.config.get("username", "neo4j"),
                    self.config.get("password", "password")
                ),
                max_connection_lifetime=self.config.get("max_connection_lifetime", 3600),
                max_connection_pool_size=self.config.get("max_connection_pool_size", 100),
                connection_acquisition_timeout=self.config.get("connection_acquisition_timeout", 60)
            )
            
            # Verify connectivity
            await self.driver.verify_connectivity()
            
            # Create constraints and indexes
            await self._create_constraints()
            
            self.connection_state = ConnectionState.CONNECTED
            logger.info("neo4j_connected")
        except (ServiceUnavailable, AuthError) as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "neo4j_connection_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise ConnectionError(f"Failed to connect to Neo4j: {str(e)}")
        except Exception as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "neo4j_initialization_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to initialize Neo4j: {str(e)}")
            
    async def _create_constraints(self):
        """Create necessary constraints and indexes in Neo4j."""
        try:
            # Create constraint on Document ID
            query = """
            CREATE CONSTRAINT document_id_unique IF NOT EXISTS
            FOR (d:Document) REQUIRE d.document_id IS UNIQUE
            """
            
            async with self.driver.session(database=self.database) as session:
                await session.run(query)
                
            # Create full-text index on content
            query = """
            CALL db.index.fulltext.createNodeIndex(
                'document_content',
                ['Document'],
                ['content']
            )
            """
            
            try:
                async with self.driver.session(database=self.database) as session:
                    await session.run(query)
            except ClientError as e:
                # Index might already exist
                if "already exists" not in str(e):
                    raise
                    
            logger.info("neo4j_constraints_created")
        except Exception as e:
            logger.error(
                "neo4j_constraint_creation_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to create Neo4j constraints: {str(e)}")
            
    async def disconnect(self):
        """Disconnect from Neo4j."""
        if self.driver is not None:
            try:
                await self.driver.close()
                self.connection_state = ConnectionState.DISCONNECTED
                logger.info("neo4j_disconnected")
            except Exception as e:
                logger.error(
                    "neo4j_disconnect_failed",
                    error=str(e),
                    error_type=type(e).__name__
                )
                
    async def get_status(self) -> ConnectionState:
        """Get the current connection status."""
        if self.connection_state != ConnectionState.CONNECTED:
            return self.connection_state
            
        try:
            # Check if connection is still alive
            await self.driver.verify_connectivity()
            return ConnectionState.CONNECTED
        except Exception:
            self.connection_state = ConnectionState.FAILED
            return ConnectionState.FAILED
            
    async def health_check(self) -> bool:
        """Check if Neo4j is healthy."""
        try:
            await self.driver.verify_connectivity()
            return True
        except Exception as e:
            logger.error(
                "neo4j_health_check_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            return False
            
    async def store_document(self, document: Document) -> str:
        """Store a document in Neo4j."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Convert metadata to a string for storage
            metadata_str = json.dumps(document.metadata)
            
            # Create document node
            query = """
            MERGE (d:Document {document_id: $document_id})
            SET d.content = $content,
                d.metadata = $metadata,
                d.created_at = $created_at,
                d.updated_at = $updated_at
            RETURN d.document_id as id
            """
            
            params = {
                "document_id": document.id,
                "content": document.content,
                "metadata": metadata_str,
                "created_at": document.created_at.isoformat() if document.created_at else datetime.datetime.now().isoformat(),
                "updated_at": document.updated_at.isoformat() if document.updated_at else datetime.datetime.now().isoformat()
            }
            
            async with self.driver.session(database=self.database) as session:
                result = await session.run(query, params)
                record = await result.single()
                document_id = record["id"]
                
            # Create relationships based on metadata
            await self._create_relationships(document)
                
            logger.info(
                "neo4j_document_stored",
                document_id=document.id
            )
            return document_id
        except TransientError as e:
            logger.error(
                "neo4j_store_document_timeout",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout storing document in Neo4j: {str(e)}")
        except Exception as e:
            logger.error(
                "neo4j_store_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error storing document in Neo4j: {str(e)}")
            
    async def _create_relationships(self, document: Document):
        """Create relationships based on document metadata."""
        try:
            # Extract entities from metadata if present
            entities = document.metadata.get("entities", [])
            if not entities:
                return
                
            # Create relationships for each entity
            for entity in entities:
                entity_type = entity.get("type", "Entity")
                entity_name = entity.get("name", "")
                entity_id = entity.get("id", str(uuid.uuid4()))
                
                if not entity_name:
                    continue
                    
                # Create entity node and relationship
                query = """
                MATCH (d:Document {document_id: $document_id})
                MERGE (e:`Entity` {id: $entity_id})
                SET e.name = $entity_name,
                    e.type = $entity_type
                MERGE (d)-[r:MENTIONS]->(e)
                RETURN e.id
                """
                
                params = {
                    "document_id": document.id,
                    "entity_id": entity_id,
                    "entity_name": entity_name,
                    "entity_type": entity_type
                }
                
                async with self.driver.session(database=self.database) as session:
                    await session.run(query, params)
                    
            logger.info(
                "neo4j_relationships_created",
                document_id=document.id,
                entity_count=len(entities)
            )
        except Exception as e:
            logger.error(
                "neo4j_relationship_creation_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            # We don't want to fail the whole operation if relationship creation fails
            # Just log the error and continue
            
    async def get_document(self, document_id: str) -> Optional[Document]:
        """Get a document from Neo4j by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            query = """
            MATCH (d:Document {document_id: $document_id})
            RETURN d.document_id as id, d.content as content, 
                   d.metadata as metadata, d.created_at as created_at,
                   d.updated_at as updated_at
            """
            
            params = {
                "document_id": document_id
            }
            
            async with self.driver.session(database=self.database) as session:
                result = await session.run(query, params)
                record = await result.single_or_none()
                
            if not record:
                logger.info(
                    "neo4j_document_not_found",
                    document_id=document_id
                )
                return None
                
            # Parse metadata
            metadata = {}
            if record["metadata"]:
                try:
                    metadata = json.loads(record["metadata"])
                except json.JSONDecodeError:
                    metadata = {}
                    
            # Create Document object
            document = Document(
                id=record["id"],
                content=record["content"],
                metadata=metadata,
                created_at=datetime.datetime.fromisoformat(record["created_at"]) if record["created_at"] else None,
                updated_at=datetime.datetime.fromisoformat(record["updated_at"]) if record["updated_at"] else None
            )
            
            logger.info(
                "neo4j_document_retrieved",
                document_id=document_id
            )
            return document
        except TransientError as e:
            logger.error(
                "neo4j_get_document_timeout",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout retrieving document from Neo4j: {str(e)}")
        except Exception as e:
            logger.error(
                "neo4j_get_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error retrieving document from Neo4j: {str(e)}")
            
    async def search_documents(
        self, 
        query: str, 
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for documents in Neo4j."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Build the search query
            if query:
                # Use full-text search
                search_query = """
                CALL db.index.fulltext.queryNodes('document_content', $query)
                YIELD node, score
                WITH node as d, score
                """
            else:
                # If no query, just match all documents
                search_query = """
                MATCH (d:Document)
                WITH d, 1.0 as score
                """
                
            # Add filters if provided
            filter_conditions = []
            if filters:
                for key, value in filters.items():
                    if isinstance(value, list):
                        # For lists, check if any value matches
                        filter_values = []
                        for i, v in enumerate(value):
                            param_name = f"filter_{key}_{i}"
                            filter_values.append(f"d.metadata CONTAINS $filter_{key}_{i}")
                            filters[param_name] = f'"{key}": "{v}"'
                        if filter_values:
                            filter_conditions.append("(" + " OR ".join(filter_values) + ")")
                    else:
                        param_name = f"filter_{key}"
                        filter_conditions.append(f"d.metadata CONTAINS ${param_name}")
                        filters[param_name] = f'"{key}": "{value}"'
                        
            if filter_conditions:
                search_query += " WHERE " + " AND ".join(filter_conditions)
                
            # Complete the query
            search_query += """
            RETURN d.document_id as id, d.content as content, 
                   d.metadata as metadata, d.created_at as created_at,
                   d.updated_at as updated_at, score
            ORDER BY score DESC
            LIMIT $limit
            """
            
            # Prepare parameters
            params = {
                "query": query,
                "limit": limit
            }
            
            # Add filter parameters
            if filters:
                params.update(filters)
                
            # Execute query
            async with self.driver.session(database=self.database) as session:
                result = await session.run(search_query, params)
                records = await result.fetch_all()
                
            # Process results
            documents = []
            for record in records:
                # Parse metadata
                metadata = {}
                if record["metadata"]:
                    try:
                        metadata = json.loads(record["metadata"])
                    except json.JSONDecodeError:
                        metadata = {}
                        
                # Add score to metadata
                if not metadata:
                    metadata = {}
                metadata["score"] = record["score"]
                
                # Create Document object
                document = Document(
                    id=record["id"],
                    content=record["content"],
                    metadata=metadata,
                    created_at=datetime.datetime.fromisoformat(record["created_at"]) if record["created_at"] else None,
                    updated_at=datetime.datetime.fromisoformat(record["updated_at"]) if record["updated_at"] else None
                )
                documents.append(document)
                
            logger.info(
                "neo4j_search_completed",
                query=query,
                result_count=len(documents)
            )
            return documents
        except TransientError as e:
            logger.error(
                "neo4j_search_timeout",
                query=query,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout searching documents in Neo4j: {str(e)}")
        except Exception as e:
            logger.error(
                "neo4j_search_failed",
                query=query,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error searching documents in Neo4j: {str(e)}")
            
    async def delete_document(self, document_id: str) -> bool:
        """Delete a document from Neo4j by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Delete document and its relationships
            query = """
            MATCH (d:Document {document_id: $document_id})
            OPTIONAL MATCH (d)-[r]-()
            DELETE r, d
            RETURN count(d) as deleted_count
            """
            
            params = {
                "document_id": document_id
            }
            
            async with self.driver.session(database=self.database) as session:
                result = await session.run(query, params)
                record = await result.single()
                deleted_count = record["deleted_count"]
                
            success = deleted_count > 0
            logger.info(
                "neo4j_document_deleted" if success else "neo4j_document_not_found_for_deletion",
                document_id=document_id,
                deleted_count=deleted_count
            )
            return success
        except TransientError as e:
            logger.error(
                "neo4j_delete_document_timeout",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout deleting document from Neo4j: {str(e)}")
        except Exception as e:
            logger.error(
                "neo4j_delete_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error deleting document from Neo4j: {str(e)}")
            
    async def update_document(self, document: Document) -> bool:
        """Update a document in Neo4j."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Convert metadata to a string for storage
            metadata_str = json.dumps(document.metadata)
            
            # Update document node
            query = """
            MATCH (d:Document {document_id: $document_id})
            SET d.content = $content,
                d.metadata = $metadata,
                d.updated_at = $updated_at
            RETURN count(d) as updated_count
            """
            
            params = {
                "document_id": document.id,
                "content": document.content,
                "metadata": metadata_str,
                "updated_at": document.updated_at.isoformat() if document.updated_at else datetime.datetime.now().isoformat()
            }
            
            async with self.driver.session(database=self.database) as session:
                result = await session.run(query, params)
                record = await result.single()
                updated_count = record["updated_count"]
                
            success = updated_count > 0
            
            if success:
                # Update relationships
                await self._update_relationships(document)
                
            logger.info(
                "neo4j_document_updated" if success else "neo4j_document_not_found_for_update",
                document_id=document.id,
                updated_count=updated_count
            )
            return success
        except TransientError as e:
            logger.error(
                "neo4j_update_document_timeout",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout updating document in Neo4j: {str(e)}")
        except Exception as e:
            logger.error(
                "neo4j_update_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error updating document in Neo4j: {str(e)}")
            
    async def _update_relationships(self, document: Document):
        """Update relationships for a document."""
        try:
            # First, delete existing relationships
            query = """
            MATCH (d:Document {document_id: $document_id})-[r:MENTIONS]->()
            DELETE r
            """
            
            params = {
                "document_id": document.id
            }
            
            async with self.driver.session(database=self.database) as session:
                await session.run(query, params)
                
            # Then create new relationships
            await self._create_relationships(document)
        except Exception as e:
            logger.error(
                "neo4j_relationship_update_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            # We don't want to fail the whole operation if relationship update fails
            # Just log the error and continue 