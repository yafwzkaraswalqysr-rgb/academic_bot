import logging,sys
from config import BASE_DIR,LOG_LEVEL

def setup_logging():
    logging.basicConfig(level=getattr(logging,LOG_LEVEL,logging.INFO),format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',handlers=[logging.StreamHandler(sys.stdout),logging.FileHandler(BASE_DIR/'bot.log',encoding='utf-8')],force=True)
