FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright BROWSER_MODE=stream
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
RUN useradd --create-home --uid 10001 agent && mkdir /app/data && chown agent:agent /app/data
COPY --chown=agent:agent main.py ./
COPY --chown=agent:agent LICENSE ./
COPY --chown=agent:agent src ./src
USER agent
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
