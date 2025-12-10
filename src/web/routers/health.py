"""
Health Check Router

System health monitoring endpoints.
"""

import logging
from fastapi import APIRouter, Depends
from datetime import datetime

from ...auth.dependencies import get_db
from ...core.database import DatabaseManager
from ...core.config import get_config
from ...graph.client import GraphAPIClient
from ...ai.claude_client import ClaudeClient
from ...jobs.queue import JobQueueManager


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Basic health check (no authentication required).

    Returns:
        Health status
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "teams-notetaker"
    }


@router.get("/health/detailed")
async def detailed_health_check(db: DatabaseManager = Depends(get_db)):
    """
    Detailed health check with component status.

    Returns:
        Detailed health status for all components
    """
    config = get_config()
    health = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "components": {}
    }

    # Check database
    try:
        with db.get_session() as session:
            # Simple query to verify connection
            session.execute("SELECT 1")
        health["components"]["database"] = {
            "status": "healthy",
            "message": "Database connection OK"
        }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        health["components"]["database"] = {
            "status": "unhealthy",
            "message": f"Database error: {str(e)}"
        }
        health["status"] = "degraded"

    # Check Graph API
    try:
        graph_client = GraphAPIClient(config.graph_api)
        graph_client.test_connection()
        health["components"]["graph_api"] = {
            "status": "healthy",
            "message": "Graph API connection OK"
        }
    except Exception as e:
        logger.error(f"Graph API health check failed: {e}")
        health["components"]["graph_api"] = {
            "status": "unhealthy",
            "message": f"Graph API error: {str(e)}"
        }
        health["status"] = "degraded"

    # Check Claude API (only if key is configured)
    if config.claude.api_key and config.claude.api_key != "your-api-key-here":
        try:
            claude_client = ClaudeClient(config.claude)
            # Don't actually call API for health check (costs money)
            # Just check that client initializes
            health["components"]["claude_api"] = {
                "status": "healthy",
                "message": "Claude API configured"
            }
        except Exception as e:
            logger.error(f"Claude API health check failed: {e}")
            health["components"]["claude_api"] = {
                "status": "unhealthy",
                "message": f"Claude API error: {str(e)}"
            }
            health["status"] = "degraded"
    else:
        health["components"]["claude_api"] = {
            "status": "not_configured",
            "message": "Claude API key not configured"
        }

    # Check job queue
    try:
        queue = JobQueueManager(db)
        stats = queue.get_queue_stats()
        health["components"]["job_queue"] = {
            "status": "healthy",
            "message": f"Queue operational ({stats['total_jobs']} jobs)",
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Job queue health check failed: {e}")
        health["components"]["job_queue"] = {
            "status": "unhealthy",
            "message": f"Job queue error: {str(e)}"
        }
        health["status"] = "degraded"

    return health
