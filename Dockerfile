FROM python:3.6.9

# Meta
LABEL maintainer="audiodude"

RUN apt-get update && apt-get install -y transmission-cli

ENV PYTHONFAULTHANDLER=1 \
  PYTHONUNBUFFERED=1 \
  PYTHONHASHSEED=random \
  PIP_NO_CACHE_DIR=off \
  PIP_DISABLE_PIP_VERSION_CHECK=on \
  PIP_DEFAULT_TIMEOUT=100 \
  POETRY_VERSION=1.0.5

# System deps:
RUN pip install "poetry==$POETRY_VERSION"

# Copy only requirements to cache them in docker layer
COPY poetry.lock pyproject.toml /app/

WORKDIR /app
# Project initialization:
RUN poetry config virtualenvs.create false \
  && poetry install --no-interaction --no-ansi

# Creating folders, and files for a project:
COPY . /app/

RUN mkdir -p /var/www/data/up
CMD python fhost.py db upgrade && gunicorn --access-logfile - --error-logfile - --capture-output -b 0.0.0.0:7321 'fhost:app'
