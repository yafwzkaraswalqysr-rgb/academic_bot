import logging,sys
from logging.handlers import RotatingFileHandler
from config import BASE_DIR,LOG_LEVEL

def setup_logging():
    file_handler = RotatingFileHandler(BASE_DIR/'bot.log', maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=getattr(logging,LOG_LEVEL,logging.INFO),format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',handlers=[logging.StreamHandler(sys.stdout),file_handler],force=True)
