import subprocess
import os
import sys
import re
import shutil
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import tempfile

# --- Konfiguration ---
# Pfad im Container, aus docker-compose.yml gemountet
TARGET_DIR_CONTAINER = os.getenv("TARGET_DIR_CONTAINER", "/mnt/podcasts")

# URLs (wie im PowerShell-Skript)
MAIN_PAGE_URL = "https://www.deutschlandfunk.de/klassik-pop-et-cetera-100.html"
BASE_URL = "https://www.deutschlandfunk.de" # Wird verwendet, falls relative URLs extrahiert werden

# Globale Pfade für Executables (im Container sind sie im PATH)
YT_DLP_EXECUTABLE = "yt-dlp"
FFMPEG_EXECUTABLE = "ffmpeg"

# Temporäres Verzeichnis für Verarbeitungsdateien
# Wir verwenden tempfile.TemporaryDirectory für automatische Bereinigung
# Alternativ: Ein fester Pfad wie TEMP_PROCESSING_DIR = "/tmp/podcast_processing"

def run_external_command(executable, arguments, workdir=None):
    """Führt einen externen Befehl aus und gibt True bei Erfolg zurück, sonst False."""
    command = [executable] + arguments
    command_str = " ".join(command) # Für die Ausgabe
    print(f"Führe aus: {command_str}", flush=True)
    try:
        process = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8', cwd=workdir)
        print(f"{executable} STDOUT:\n{process.stdout}", flush=True)
        if process.stderr:
            print(f"{executable} STDERR:\n{process.stderr}", flush=True)
        print(f"{executable} erfolgreich abgeschlossen.", flush=True)
        return True, process.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Fehler: {executable} wurde mit Fehlercode {e.returncode} beendet.", flush=True)
        print(f"{executable} STDOUT:\n{e.stdout}", flush=True)
        print(f"{executable} STDERR:\n{e.stderr}", flush=True)
        return False, e.stderr.strip()
    except FileNotFoundError:
        print(f"Fehler: {executable} nicht gefunden. Ist es im Docker-Image korrekt installiert und im PATH?", flush=True)
        return False, f"{executable} not found."
    except Exception as e:
        print(f"Unerwarteter Fehler beim Ausführen von {executable}: {e}", flush=True)
        return False, str(e)

def sanitize_filename_component(name_component):
    """Bereinigt einen String, um ihn als Teil eines Dateinamens sicher zu verwenden."""
    # Entferne ungültige Zeichen (Windows-Perspektive als strengste Annahme, aber anpassbar)
    # In Linux sind weniger Zeichen ungültig, aber / darf nicht vorkommen.
    # Für Cross-Plattform-Sicherheit oder Docker-interne Namen ist es gut, restriktiv zu sein.
    name_component = re.sub(r'[\\/*?:"<>|]', "_", name_component)
    name_component = name_component.replace("\n", "_").replace("\r", "_")
    return name_component.strip()


def main():
    """Hauptlogik des Skripts."""
    print("Starte Podcast Download und Tagging Skript...", flush=True)

    # Sicherstellen, dass der Zielordner im Container existiert
    try:
        os.makedirs(TARGET_DIR_CONTAINER, exist_ok=True)
        print(f"Zielordner (im Container): {TARGET_DIR_CONTAINER}", flush=True)
    except Exception as e:
        print(f"Fehler beim Erstellen des Zielordners {TARGET_DIR_CONTAINER}: {e}", flush=True)
        sys.exit(1)

    # Temporäres Verzeichnis für alle Operationen erstellen
    with tempfile.TemporaryDirectory(prefix="podcast_dl_") as temp_dir:
        print(f"Temporäres Verzeichnis erstellt: {temp_dir}", flush=True)

        # 1. Lade Hauptseite
        print(f"1. Lade Hauptseite: {MAIN_PAGE_URL}", flush=True)
        try:
            main_page_response = requests.get(MAIN_PAGE_URL, timeout=20)
            main_page_response.raise_for_status()
            main_page_html = main_page_response.text
        except requests.exceptions.RequestException as e:
            print(f"Fehler beim Laden der Hauptseite {MAIN_PAGE_URL}: {e}", flush=True)
            sys.exit(1)

        # 2. Finde Link zur neuesten Episode
        print("2. Suche Link zur neuesten Episode...", flush=True)
        # Regex aus PowerShell: (?s)<article class="b-article-teaser.*?<a href="(?<relativeUrl>[^"]+)"
        # Python's re.search findet den ersten Treffer. re.DOTALL entspricht (?s)
        match = re.search(r'<article class="b-article-teaser.*?<a href="(?P<relativeUrl>[^"]+)"', main_page_html, re.DOTALL)

        if not match:
            print("Konnte den Link zur neuesten Episode auf der Hauptseite nicht finden. Seitenstruktur geändert?", flush=True)
            # Versuch mit BeautifulSoup als Fallback oder primäre Methode
            soup_main = BeautifulSoup(main_page_html, 'html.parser')
            article_teaser = soup_main.find('article', class_='b-article-teaser')
            episode_link_tag = None
            if article_teaser:
                # Suchen nach einem Link innerhalb des Teasers. Annahme: der erste passende Link
                # Die genaue Klasse oder Struktur muss evtl. angepasst werden.
                # PS-Skript erwähnt 'teaser-title', aber die Regex ist allgemeiner.
                episode_link_tag = article_teaser.find('a', href=True) # Nimmt den ersten Link mit href

            if not episode_link_tag or not episode_link_tag.get('href'):
                 print("Auch mit BeautifulSoup keinen passenden Episodenlink gefunden.", flush=True)
                 sys.exit(1)
            relative_episode_url = episode_link_tag['href']
        else:
            relative_episode_url = match.group('relativeUrl')

        # URL zusammensetzen (falls relativ)
        if relative_episode_url.startswith('/'):
            episode_url = BASE_URL + relative_episode_url
        else: # Falls es schon eine volle URL ist
            episode_url = relative_episode_url
        print(f"   Link zur neuesten Episode gefunden: {episode_url}", flush=True)

        # 3. Lade Episodenseite für Metadaten
        print(f"3. Lade Episodenseite für Metadaten: {episode_url}", flush=True)
        try:
            episode_page_response = requests.get(episode_url, timeout=20)
            episode_page_response.raise_for_status()
            episode_html_content = episode_page_response.text
            soup_episode = BeautifulSoup(episode_html_content, 'html.parser')
        except requests.exceptions.RequestException as e:
            print(f"Fehler beim Laden der Episodenseite {episode_url}: {e}", flush=True)
            sys.exit(1)

        # 4. Extrahiere Metadaten
        print("4. Extrahiere Metadaten...", flush=True)
        try:
            # Künstlername / Episodentitel (aus <span class="headline-kicker">)
            kicker_element = soup_episode.find('span', class_='headline-kicker')
            meta_episode_title = kicker_element.text.strip() if kicker_element else "Unbekannter Sendungstitel"

            # Untertitel (aus <span class="headline-title">) -> wird für 'description' Tag verwendet
            headline_title_element = soup_episode.find('span', class_='headline-title')
            meta_subtitle_for_description_tag = headline_title_element.text.strip() if headline_title_element else ""

            # Beschreibung (aus <p class="article-header-description">) -> wird für 'comment' Tag verwendet
            desc_element = soup_episode.find('p', class_='article-header-description')
            meta_description_for_comment_tag = desc_element.text.strip() if desc_element else ""
            meta_description_for_comment_tag = re.sub(r'\s{2,}', ' ', meta_description_for_comment_tag) # Mehrere Leerzeichen ersetzen

            # Datum (aus <time>)
            time_element = soup_episode.find('time')
            date_str_raw = time_element.text.strip() if time_element else ""
            date_match_obj = re.search(r'(\d{2}\.\d{2}\.\d{4})', date_str_raw)
            meta_iso_date_str = ""
            if date_match_obj:
                try:
                    parsed_date = datetime.strptime(date_match_obj.group(1), '%d.%m.%Y')
                    meta_iso_date_str = parsed_date.strftime('%Y-%m-%d')
                except ValueError:
                    print(f"Konnte Datum '{date_match_obj.group(1)}' nicht parsen. Verwende aktuelles Datum.", flush=True)
                    meta_iso_date_str = datetime.now().strftime('%Y-%m-%d')
            else:
                print(f"Kein Datum im Format dd.MM.yyyy in '{date_str_raw}' gefunden. Verwende aktuelles Datum.", flush=True)
                meta_iso_date_str = datetime.now().strftime('%Y-%m-%d')

            print("   Metadaten extrahiert:", flush=True)
            print(f"     Sendungstitel (für Dateiname & title-Tag): {meta_episode_title}", flush=True)
            print(f"     Untertitel (für description-Tag):         {meta_subtitle_for_description_tag}", flush=True)
            print(f"     Beschreibung (für comment-Tag):          {meta_description_for_comment_tag[:80]}...", flush=True)
            print(f"     Datum (ISO):                            {meta_iso_date_str}", flush=True)

        except Exception as e:
            print(f"Fehler beim Extrahieren der Metadaten: {e}", flush=True)
            sys.exit(1)

        # Bereinige Sendungstitel für Dateinamen
        safe_filename_episode_title = sanitize_filename_component(meta_episode_title)

        # Konstruiere finalen Dateinamen und Pfad im Zielverzeichnis
        final_filename = f"{meta_iso_date_str} {safe_filename_episode_title}.m4a"
        final_filepath_in_target_dir = os.path.join(TARGET_DIR_CONTAINER, final_filename)
        print(f"   Finaler Dateiname: {final_filename}", flush=True)
        print(f"   Zielpfad (im Container): {final_filepath_in_target_dir}", flush=True)

        # 5. Prüfe, ob Zieldatei bereits existiert
        print(f"5. Prüfe, ob Zieldatei bereits existiert: {final_filepath_in_target_dir}", flush=True)
        if os.path.exists(final_filepath_in_target_dir):
            print("   Datei existiert bereits. Download wird übersprungen.", flush=True)
            sys.exit(0)
        else:
            print("   Datei existiert noch nicht.", flush=True)

        # Pfade für temporäre Dateien (Download, Metadaten, getaggte Ausgabe)
        temp_download_path_m4a = os.path.join(temp_dir, "downloaded_audio_temp.m4a")
        temp_metadata_filepath = os.path.join(temp_dir, "metadata.txt")
        temp_tagged_output_path_m4a = os.path.join(temp_dir, "tagged_audio_temp.m4a")

        # 6. Lade Episode mit yt-dlp herunter
        print(f"6. Lade Episode herunter nach: {temp_download_path_m4a}", flush=True)
        yt_dlp_args = [
            "-f", "m4a",                     # Format M4A
            "--output", temp_download_path_m4a, # Ausgabe-Dateipfad
            episode_url                      # URL der Episode
        ]
        success, _ = run_external_command(YT_DLP_EXECUTABLE, yt_dlp_args)
        if not success or not os.path.exists(temp_download_path_m4a):
            print("yt-dlp Download fehlgeschlagen oder Datei nicht erstellt.", flush=True)
            # Bereinigung des temp_dir erfolgt automatisch durch with-Statement
            sys.exit(1)

        # 7. Schreibe Metadaten für ffmpeg
        print(f"7. Erstelle FFMETADATA-Datei: {temp_metadata_filepath}", flush=True)
        # Anführungszeichen und Sonderzeichen im PowerShell-Skript mit -replace "&quot;", '"' behandelt.
        # Python's HTML-Parser (BeautifulSoup) sollte dies bereits erledigen (gibt unescaped Text zurück).
        # Wir müssen aber Zeilenumbrüche und Backslashes für FFMETADATA escapen.
        def escape_for_ffmetadata(text):
            if text is None: return ""
            return text.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('=', '\\=')

        ffmetadata_content = [
            ";FFMETADATA1",
            f"album=Klassik, Pop et cetera",
            f"artist=Deutschlandfunk", # Wie im PS-Skript hartkodiert
            f"title={escape_for_ffmetadata(meta_episode_title)}",
            f"description={escape_for_ffmetadata(meta_subtitle_for_description_tag)}", # PS: Untertitel -> description
            f"comment={escape_for_ffmetadata(meta_description_for_comment_tag)}",    # PS: Beschreibung -> comment
            f"date={escape_for_ffmetadata(meta_iso_date_str)}",
            f"copyright={escape_for_ffmetadata(episode_url)}", # URL als Copyright
            f"show=Klassik, Pop et cetera"
        ]
        try:
            with open(temp_metadata_filepath, 'w', encoding='utf-8') as f:
                f.write("\n".join(ffmetadata_content))
            print("   FFMETADATA-Datei erfolgreich geschrieben.", flush=True)
        except IOError as e:
            print(f"Fehler beim Schreiben der Metadaten-Datei {temp_metadata_filepath}: {e}", flush=True)
            sys.exit(1)

        # 8. Tagge mit ffmpeg
        print(f"8. Tagge Audiodatei mit ffmpeg (Ausgabe nach: {temp_tagged_output_path_m4a})", flush=True)
        ffmpeg_args = [
            "-i", temp_download_path_m4a,        # Eingabe-Audiodatei (geändert von PS, erst Audio, dann Metadaten-Datei)
            "-i", temp_metadata_filepath,       # Eingabe-Metadatendatei
            "-map_metadata", "1",               # Metadaten vom zweiten Input (metadata.txt)
            "-map", "0:a",                      # Audiostreams vom ersten Input (audio.m4a)
            "-codec", "copy",                   # Keine Neukodierung
            "-y",                               # Überschreibe Ausgabedatei, falls vorhanden
            temp_tagged_output_path_m4a         # Ausgabedatei
        ]
        # Hinweis: Die Reihenfolge von -i und die -map Parameter sind wichtig.
        # PS-Script: -i metadata -i audio -map_metadata 0 -map 1
        # Hier angepasst: -i audio -i metadata -map_metadata 1 -map 0:a (map bezieht sich auf Input-Index)

        success, _ = run_external_command(FFMPEG_EXECUTABLE, ffmpeg_args)
        if not success or not os.path.exists(temp_tagged_output_path_m4a):
            print("ffmpeg Metadaten-Tagging fehlgeschlagen oder Datei nicht erstellt.", flush=True)
            sys.exit(1)

        # 9. Verschiebe die fertige, getaggte Datei ins Zielverzeichnis
        print(f"9. Verschiebe getaggte Datei nach: {final_filepath_in_target_dir}", flush=True)
        try:
            shutil.move(temp_tagged_output_path_m4a, final_filepath_in_target_dir)
            print(f"   Datei erfolgreich nach {final_filepath_in_target_dir} verschoben.", flush=True)
        except Exception as e:
            print(f"Fehler beim Verschieben der Datei {temp_tagged_output_path_m4a} nach {final_filepath_in_target_dir}: {e}", flush=True)
            print(f"Die getaggte Datei befindet sich möglicherweise noch in: {temp_tagged_output_path_m4a}", flush=True)
            sys.exit(1)

        # Temporäre Original-Downloaddatei (ohne Tags) wird nicht mehr explizit gelöscht, da temp_dir alles bereinigt.
        # Der metadata.txt wird auch durch temp_dir bereinigt.

    print("Skript erfolgreich abgeschlossen.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    # Setze Umgebungsvariablen für Testzwecke, falls nicht über Docker Compose gesetzt
    # Beispiel:
    # os.environ["TARGET_DIR_CONTAINER"] = "./data_test/podcasts"
    # os.environ["PODCAST_DOWNLOAD_URL"] = "ECHTE_DLF_EPISODEN_URL_FUER_YT-DLP" # z.B. die Audio-URL von der Episodenseite
    # os.environ["METADATA_WEBSITE_URL"] = "ECHTE_DLF_EPISODEN_SEITEN_URL" # Die URL, die im Browser angezeigt wird

    # Zum Testen: Erstelle einen data_test Ordner, falls du TARGET_DIR_CONTAINER darauf umbiegst.
    # if not os.path.exists("./data_test/podcasts"):
    #    os.makedirs("./data_test/podcasts", exist_ok=True)
    main()
