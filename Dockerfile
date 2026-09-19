FROM python:3.12-slim
WORKDIR /app
COPY app.py collector.py ./
COPY docs/index.html ./docs/index.html
ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
USER 65532:65532
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/api/health',timeout=3)"
CMD ["python", "app.py"]
