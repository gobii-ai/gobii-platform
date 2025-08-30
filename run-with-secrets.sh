#!/bin/bash
# Helper script to run Django and Celery with secrets support

export GOBII_ENCRYPTION_KEY="test-encryption-key-for-local-dev"

echo "Starting with GOBII_ENCRYPTION_KEY set..."

if [ "$1" = "django" ]; then
    echo "Starting Django server..."
    python manage.py runserver
elif [ "$1" = "celery" ]; then
    echo "Starting Celery worker..."
    celery -A config worker -l info --pool solo
else
    echo "Usage: $0 [django|celery]"
    exit 1
fi 