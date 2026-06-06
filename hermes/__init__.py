"""
Hermes - 赛博游民数字管家
A portable Mavis-compatible agent with hybrid LLM routing.
"""

__version__ = "2.0.0"
__author__ = "Hermes Project"

from hermes.config import HermesConfig, load_config
from hermes.agent import HermesAgent

__all__ = ["HermesAgent", "HermesConfig", "load_config", "__version__"]
