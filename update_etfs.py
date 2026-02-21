import logging
import sys
from emotion_validation.etf_rotation.etf_data import ETFDataManager

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

print("Starting ETF update...")
manager = ETFDataManager()
manager.fetch_all(start_date="20240101")
print("Done!")
