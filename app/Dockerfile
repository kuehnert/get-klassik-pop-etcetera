# Wähle ein passendes Python Basis-Image
FROM python:3.11-slim-bullseye

# Installiere ffmpeg (wird von yt-dlp -x für Audioextraktion/-konvertierung oft
# benötigt) und andere eventuell nützliche Tools wie ca-certificates
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Setze das Arbeitsverzeichnis im Container
WORKDIR /app

# Kopiere die requirements.txt Datei und installiere die Abhängigkeiten
# --no-cache-dir reduziert die Image-Größe
# --trusted-host pypi.python.org und --trusted-host pypi.org und --trusted-host files.pythonhosted.org
# können bei Netzwerkproblemen in manchen Umgebungen helfen, sind aber nicht immer nötig.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere den Rest des Anwendungscodes
COPY . .

# Definiere den Standardbefehl, der beim Starten des Containers ausgeführt wird
# Dieser Befehl startet dein Python-Skript.
CMD ["python", "main.py"]
