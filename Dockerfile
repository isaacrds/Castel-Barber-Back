# Usa uma versão leve e oficial do Python
FROM python:3.11-slim

# Impede o Python de gerar arquivos .pyc e força os logs a aparecerem na hora
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Define a pasta de trabalho dentro do servidor
WORKDIR /app

# Copia os requirements primeiro (otimiza o tempo de build do Docker)
COPY requirements.txt .

# Instala as dependências
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o restante do seu código para dentro do servidor
COPY . .

# A porta padrão do Cloud Run é a 8080
ENV PORT=8080

# Inicia o servidor uvicorn liberando o acesso externo (0.0.0.0) na porta do Google
CMD ["sh", "-c", "uvicorn api_castel:app --host 0.0.0.0 --port ${PORT}"]