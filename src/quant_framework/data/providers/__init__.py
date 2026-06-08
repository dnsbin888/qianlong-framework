"""Data provider adapters.

Each module provides a DataProvider implementation for a specific data source.
All providers self-register with the DataProviderRegistry on import.
"""

from quant_framework.data.providers.akshare import AKShareDataProvider
from quant_framework.data.providers.simulated import SimulatedDataProvider
from quant_framework.data.providers.ths import THSDataProvider
from quant_framework.data.providers.ths_day import THSDayDataProvider
from quant_framework.data.providers.tushare import TushareDataProvider
from quant_framework.data.registry import DataProviderRegistry

# Self-register all providers by their canonical names
DataProviderRegistry.register("ths", THSDataProvider)
DataProviderRegistry.register("ths_day", THSDayDataProvider)
DataProviderRegistry.register("tushare", TushareDataProvider)
DataProviderRegistry.register("akshare", AKShareDataProvider)
DataProviderRegistry.register("simulated", SimulatedDataProvider)
DataProviderRegistry.register("csv", SimulatedDataProvider)  # CSV alias for SimulatedDataProvider

__all__ = [
    "THSDataProvider",
    "THSDayDataProvider",
    "TushareDataProvider",
    "AKShareDataProvider",
    "SimulatedDataProvider",
]
