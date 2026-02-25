# -*- coding: utf-8 -*-
from __future__ import annotations

import logging, logging.handlers
import os
from .settings import _get_cache_dir  # type: ignore

_LOGGER_INITIALIZED = False


def get_logger() -> logging.Logger:
	global _LOGGER_INITIALIZED
	logger = logging.getLogger("houdini.mcp")
	if _LOGGER_INITIALIZED:
		return logger
	logger.setLevel(logging.INFO)
	try:
		log_dir = _get_cache_dir()
		os.makedirs(log_dir, exist_ok=True)
		log_path = os.path.join(log_dir, "houdini_mcp.log")
		handler = logging.handlers.RotatingFileHandler(
			log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
		)
		fmt = logging.Formatter(
			"%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
		)
		handler.setFormatter(fmt)
		logger.addHandler(handler)
		console = logging.StreamHandler()
		console.setFormatter(fmt)
		logger.addHandler(console)
		_LOGGER_INITIALIZED = True
	except Exception:
		pass
	return logger
