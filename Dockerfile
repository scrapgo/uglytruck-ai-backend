FROM python:3.11-slim

# set correct working directory
WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the whole project into /app
COPY . .

# Git commit of the built code, shown by GET /health (.git is not copied into the image).
# Build with: GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

EXPOSE 9160

CMD ["uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "9160"]
