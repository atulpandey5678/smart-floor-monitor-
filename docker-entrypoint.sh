#!/bin/sh
set -e

echo "=== Cologic Shop Floor Tracker — Container Starting ==="

# Bind to 0.0.0.0 inside the container so external connections are accepted
export API_HOST="${API_HOST:-0.0.0.0}"

# Run database migrations before accepting requests
echo "Applying database migrations..."
python -c "
from db.migrations import MigrationRunner
import os

db_path = os.environ.get('DB_PATH', 'tracker.db')
runner = MigrationRunner(db_path)
applied = runner.run()
if applied:
    print(f'Applied {len(applied)} migration(s)')
else:
    print('Database is up to date')
runner.close()
"

echo "Migrations complete. Starting application..."

# Start the application
exec python main.py
