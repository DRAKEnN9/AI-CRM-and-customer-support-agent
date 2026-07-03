from fastapi import FastAPI
from app.routers import whatsapp, admin
from app.config import settings
from app.database import engine, Base
from app.models import salon, knowledge # Import models to register them with Base
from sqlalchemy import text
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    description="AI Customer Support Agent for Hair Salon",
    version="0.1.0"
)

# Include Routers
app.include_router(whatsapp.router)
app.include_router(admin.router)

@app.get("/")
async def root():
    return {"message": "Salon AI Backend is running", "status": "healthy"}

@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting {settings.APP_NAME} in {settings.ENVIRONMENT} mode...")

    # Create tables and extensions automatically on startup
    try:
        async with engine.begin() as conn:
            # 1. Create pgvector extension (required for KnowledgeDocument)
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            # 2. Create all defined tables
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables and extensions created successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
