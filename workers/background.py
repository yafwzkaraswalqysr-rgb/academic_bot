import asyncio,logging
from services.file_service import cleanup_temp_files
async def background_tasks():
 while True:
  try:await cleanup_temp_files()
  except Exception as e:logging.getLogger(__name__).warning('cleanup: %s',e)
  await asyncio.sleep(3600)
