from flowgentic.utils.logger.logger import Logger


def test_logger_shutdown_is_idempotent() -> None:
    logger = Logger(colorful_output=False)

    assert logger.queue_handler in logger.root_logger.handlers

    logger.shutdown()
    logger.shutdown()

    assert logger.listener is None
    assert logger.queue_handler not in logger.root_logger.handlers
