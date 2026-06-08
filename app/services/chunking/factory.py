from app.config import logger
from app.services.chunking.auto import AutoChunkingService
from app.services.chunking.config import ChunkingConfig
from app.services.chunking.paragraph import ParagraphChunkingService
from app.services.chunking.recursive import RecursiveChunkingService


def get_chunking_service(config: ChunkingConfig):
    if config.strategy == "auto":
        return AutoChunkingService(config)
    if config.strategy == "paragraph":
        return ParagraphChunkingService(config)
    if config.strategy == "recursive":
        return RecursiveChunkingService(config)

    logger.warning(
        "Unknown chunking strategy '%s'; falling back to recursive", config.strategy
    )
    return RecursiveChunkingService(config, strategy_name="recursive")
