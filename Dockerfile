FROM python:3.11-slim

# set correct working directory
WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the whole project into /app
COPY . .

EXPOSE 9160

CMD ["uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "9160"]
