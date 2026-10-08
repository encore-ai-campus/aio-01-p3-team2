FROM python:3.12-slim

WORKDIR /app

# Build with mcp_server/ as the Docker context.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/mcp_server
EXPOSE 8010

CMD ["python", "-m", "mcp_server.server"]
