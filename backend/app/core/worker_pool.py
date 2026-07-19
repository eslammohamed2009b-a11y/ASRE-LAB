from concurrent.futures import ProcessPoolExecutor

# Shared process pool used by computational endpoints.
worker_pool = ProcessPoolExecutor()
