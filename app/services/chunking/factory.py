from app.config import logger
from app.services.chunking.config import ChunkingConfig
from app.services.chunking.recursive import RecursiveChunkingService


def get_chunking_service(config: ChunkingConfig):
    if config.strategy in {"recursive", "auto"}:
        return RecursiveChunkingService(config, strategy_name=config.strategy)

    logger.warning(
        "Unknown chunking strategy '%s'; falling back to recursive", config.strategy
    )
    return RecursiveChunkingService(config, strategy_name="recursive")
