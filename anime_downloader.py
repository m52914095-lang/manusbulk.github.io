import os
import requests
import xml.etree.ElementTree as ET
import subprocess
import time
import json
import logging
import re
from urllib.parse import quote_plus

# Configure logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Configuration --- #
DOODSTREAM_API_KEY = "554366xrjxeza9m7e4m02v"
STREAMP2P_API_KEY = "2a82d855a5801d4f32c498f8"

# Jikan API (MyAnimeList) base URL
JIKAN_API_BASE = "https://api.jikan.moe/v4"

# Nyaa.si base URL and search parameters
NYAA_SI_BASE = "https://nyaa.si"
NYAA_SI_RSS_PATH = "/?page=rss&f=0&s=seeders&o=desc&q=" # Removed category filter from RSS path for broader search

# --- Helper Functions --- #
def run_command(command, shell=False, check=True, capture_output=True, text=True, timeout=None, use_sudo=False):
    if use_sudo:
        if isinstance(command, list):
            command = ["sudo"] + command
        else:
            command = "sudo " + command
    logging.info("Executing command: {}".format(" ".join(command) if isinstance(command, list) else command))
    try:
        result = subprocess.run(command, shell=shell, check=check, capture_output=capture_output, text=text, timeout=timeout)
        if result.stdout:
            logging.debug("Command stdout: {}".format(result.stdout))
        if result.stderr:
            logging.error("Command stderr: {}".format(result.stderr))
        return result
    except subprocess.CalledProcessError as e:
        logging.error("Command failed with error: {}. Stdout: {}. Stderr: {}".format(e.returncode, e.stdout, e.stderr))
        raise
    except subprocess.TimeoutExpired as e:
        logging.error("Command timed out: {}".format(e))
        raise
    except Exception as e:
        logging.error("Error executing command: {}".format(e))
        raise

def get_disk_space():
    st = os.statvfs("/")
    free_bytes = st.f_bavail * st.f_frsize
    return free_bytes / (1024**3) # Return in GB

def cleanup_disk_space():
    logging.info("Starting disk cleanup...")
    commands = [
        "apt-get clean",
        "apt-get autoremove -y",
        "rm -rf /var/cache/apt/archives",
        "rm -rf /var/lib/apt/lists/*",
        "rm -rf /tmp/*",
        "rm -rf ~/.cache",
        "rm -rf ~/.npm",
        "rm -rf ~/.yarn",
        "rm -rf ~/.gradle",
        "rm -rf ~/.m2",
        "rm -rf ~/.local/share/Trash"
    ]
    for cmd in commands:
        try:
            run_command(cmd, shell=True, use_sudo=True)
        except Exception as e:
            logging.warning("Cleanup command \'{}\' failed: {}".format(cmd, e))
    free_space = get_disk_space()
    logging.info("Disk cleanup complete. Free space: {:.2f} GB".format(free_space))
    return free_space

# --- Jikan API Functions --- #
def search_anime_jikan(anime_name):
    logging.info("Searching Jikan API for anime: {}".format(anime_name))
    search_url = "{}/anime?q={}&sfw".format(JIKAN_API_BASE, quote_plus(anime_name))
    response = requests.get(search_url, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data and data["data"]:
        priority_order = {"TV": 0, "Movie": 1, "OVA": 2, "ONA": 3, "Special": 4}
        sorted_results = sorted(data["data"], key=lambda x: priority_order.get(x.get("type"), 99))
        return sorted_results[0]
    return None

def get_anime_details_jikan(anime_id):
    logging.info("Fetching details for anime ID: {}".format(anime_id))
    details_url = "{}/anime/{}/full".format(JIKAN_API_BASE, anime_id)
    response = requests.get(details_url, timeout=30)
    response.raise_for_status()
    return response.json()["data"]

def get_anime_episodes_jikan(anime_id):
    logging.info("Fetching episodes for anime ID: {}".format(anime_id))
    episodes_url = "{}/anime/{}/episodes".format(JIKAN_API_BASE, anime_id)
    all_episodes = []
    page = 1
    while True:
        response = requests.get("{}?page={}".format(episodes_url, page), timeout=30)
        response.raise_for_status()
        data = response.json()
        if not data["data"]:
            break
        all_episodes.extend(data["data"])
        if not data["pagination"]["has_next_page"]:
            break
        page += 1
        time.sleep(0.2)
    return all_episodes

# --- Nyaa.si Functions --- #
def search_nyaa_si(query):
    logging.info("Searching Nyaa.si for query: {}".format(query))
    logging.debug("Nyaa.si search URL: {}{}{}".format(NYAA_SI_BASE, NYAA_SI_RSS_PATH, quote_plus(query)))
    search_url = "{}{}{}".format(NYAA_SI_BASE, NYAA_SI_RSS_PATH, quote_plus(query))
    response = requests.get(search_url, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    all_torrents = []
    for item in root.findall(".//item"):
        title = item.find("title").text
        link = item.find("link").text
        
        magnet_link = None
        description_cdata = item.find("description").text
        magnet_match = re.search(r"(magnet:\?xt=urn:[a-z0-9]+:[a-z0-9]{40}.*?)", description_cdata)
        if magnet_match:
            magnet_link = magnet_match.group(1)
        
        download_link = magnet_link if magnet_link else link

        seeders = int(item.find("{https://nyaa.si/xmlns/nyaa}seeders").text)
        leechers = int(item.find("{https://nyaa.si/xmlns/nyaa}leechers").text)
        size = item.find("{https://nyaa.si/xmlns/nyaa}size").text
        category = item.find("{https://nyaa.si/xmlns/nyaa}category").text

        torrent_info = {
            "title": title,
            "link": download_link,
            "seeders": seeders,
            "leechers": leechers,
            "size": size,
            "category": category
        }
        all_torrents.append(torrent_info)
        logging.debug("Found torrent (before filtering): Title=\'{}\', Category=\'{}\'".format(title, category))
    logging.debug("Total torrents found before filtering: {}".format(len(all_torrents)))

    # Filter for English-translated anime. Broaden criteria.
    filtered_torrents = []
    for torrent in all_torrents:
        # Check for English in title, category, or if it's a subbed release
        if "English-translated" in torrent["category"] or \
           "English" in torrent["title"] or \
           "Sub" in torrent["title"] or \
           "Dual Audio" in torrent["title"]:
            filtered_torrents.append(torrent)
    logging.debug("Total torrents found after filtering: {}".format(len(filtered_torrents)))
    
    filtered_torrents.sort(key=lambda x: x["seeders"], reverse=True)
    return filtered_torrents

# --- Torrent Downloading (aria2c) --- #
def download_torrent_aria2c(magnet_link, output_dir, max_retries=3, stall_timeout=300):
    logging.info("Attempting to download: {} to {}".format(magnet_link, output_dir))
    os.makedirs(output_dir, exist_ok=True)

    command = [
        "aria2c",
        "--seed-time=0",
        "--max-overall-download-limit=0",
        "--max-connection-per-server=16",
        "--split=16",
        "--bt-max-peers=100",
        "--dir", output_dir,
        "--allow-overwrite=true", # Allow overwriting existing files
        magnet_link
    ]
    logging.debug("aria2c command: {}".format(" ".join(command)))

    for attempt in range(max_retries):
        logging.info("Download attempt {}/{} for {}".format(attempt + 1, max_retries, magnet_link))
        try:
            result = run_command(command, timeout=stall_timeout)
            if result.returncode == 0:
                logging.info("Successfully downloaded {}".format(magnet_link))
                return True
            else:
                logging.error("aria2c download failed with code {}. Stdout: {}. Stderr: {}".format(result.returncode, result.stdout, result.stderr))
        except subprocess.TimeoutExpired:
            logging.warning("Download for {} timed out after {} seconds.".format(magnet_link, stall_timeout))
        except Exception as e:
            logging.error("Error during aria2c download: {}".format(e))
        
        if attempt < max_retries - 1:
            logging.info("Retrying download...")
        else:
            logging.error("Failed to download {} after {} attempts.".format(magnet_link, max_retries))
            return False
    return False

# --- Hard-subbing (ffmpeg) --- #
def get_subtitle_track(video_path):
    logging.info("Probing video for subtitle tracks: {}".format(video_path))
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "s",
            "-show_entries", "stream=index,codec_name,tags:stream_tags",
            "-of", "json",
            video_path
        ]
        result = run_command(cmd, check=False) # Allow ffprobe to fail if no subtitle streams
        if result.returncode != 0:
            logging.warning("ffprobe failed to find subtitle streams or encountered an error: {}".format(result.stderr))
            return None

        streams_info = json.loads(result.stdout)
        
        if "streams" in streams_info:
            for stream in streams_info["streams"]:
                # Check for English language tag or common subtitle codec names
                if stream.get("tags", {}).get("language", "").lower() == "eng" or \
                   stream.get("codec_name") in ["ass", "srt", "subrip", "mov_text"]:
                    logging.info("Found subtitle track at index: {} (Codec: {}, Language: {})".format(
                        stream.get("index"), stream.get("codec_name"), stream.get("tags", {}).get("language")))
                    return stream.get("index")
        logging.info("No suitable embedded subtitle track found.")
        return None
    except Exception as e:
        logging.error("Error probing for subtitle tracks: {}".format(e))
        return None

def hardsub_video(input_video_path, output_path, external_subtitle_path=None, video_has_embedded_subs=False):
    logging.info("Starting hard-subbing for: {} to {}".format(input_video_path, output_path))
    subtitle_track_index = get_subtitle_track(input_video_path)
    
    # Construct the subtitles filter string carefully
    subtitles_filter = None
    if external_subtitle_path and os.path.exists(external_subtitle_path):
        subtitles_filter = f"subtitles='{external_subtitle_path}'"
        logging.info("Using external subtitle file for hard-subbing.")
    elif subtitle_track_index is not None:
        subtitles_filter = f"subtitles='{input_video_path}':si={subtitle_track_index}"
        logging.info("Using embedded subtitle track for hard-subbing.")
    elif video_has_embedded_subs: # If ffprobe found subs but couldn't identify English, try without si
        subtitles_filter = f"subtitles='{input_video_path}'"
        logging.warning("Attempting hard-subbing with embedded subtitles without specific track index.")
    else:
        logging.warning("No suitable subtitle source found for hard-subbing. Proceeding without subtitles.")

    command = [
        "ffmpeg",
        "-y", # Overwrite output files without asking
        "-i", input_video_path,
    ]
    if subtitles_filter:
        command.extend([ "-vf", subtitles_filter ])
    
    command.extend([
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "copy",
        output_path
    ])

    try:
        run_command(command, timeout=300)
        logging.info("Hard-subbing complete for: {}".format(input_video_path))
        return True
    except Exception as e:
        logging.error("Hard-subbing failed for {} with error: {}".format(input_video_path, e))
        return False

# --- Upload Functions --- #
def upload_to_doodstream(file_path):
    logging.info("Uploading {} to DoodStream...".format(file_path))
    upload_url = "https://doodapi.co/api/upload/server?key={}".format(DOODSTREAM_API_KEY)
    
    try:
        # Get upload server URL
        response = requests.get(upload_url, timeout=30)
        response.raise_for_status()
        server_data = response.json()
        if server_data["status"] != 200:
            logging.error("Failed to get DoodStream upload server: {}".format(server_data.get("msg")))
            return None
        
        final_upload_url = server_data["result"] + "?key=" + DOODSTREAM_API_KEY
        
        # Upload file
        with open(file_path, "rb") as f:
            files = {"file": f}
            data = {"api_key": DOODSTREAM_API_KEY}
            response = requests.post(final_upload_url, files=files, data=data, timeout=300)
            response.raise_for_status()
            try:
                upload_data = response.json()
            except json.JSONDecodeError:
                logging.error("DoodStream upload response is not valid JSON: {}".format(response.text))
                return None
            
            if upload_data["status"] == 200 and upload_data["result"] and isinstance(upload_data["result"], list) and len(upload_data["result"]) > 0:
                file_code = upload_data["result"][0]["filecode"]
                logging.info("DoodStream upload successful: {}".format(file_code))
                return "https://doodstream.com/e/{}".format(file_code)
            else:
                logging.error("DoodStream upload failed: {}".format(upload_data.get("msg")))
                return None
    except Exception as e:
        logging.error("Error uploading to DoodStream: {}".format(e))
        return None

def upload_to_streamp2p(file_path):
    logging.info("Uploading {} to StreamP2P...".format(file_path))
    # StreamP2P API requires a login to get an upload URL, or a direct upload endpoint.
    # Based on the API document, it's assumed to be a direct file upload to a specific endpoint.
    # The API key is likely used for authentication in the request.

    # Step 1: Get upload server URL
    get_upload_url = "https://streamp2p.com/api/v1/video/upload"
    headers = {"Authorization": f"Bearer {STREAMP2P_API_KEY}"}
    
    try:
        response = requests.get(get_upload_url, headers=headers, timeout=30)
        response.raise_for_status()
        server_data = response.json()

        if server_data.get("status") == "OK" and server_data.get("result") and server_data["result"].get("url"):
            streamp2p_upload_url = server_data["result"]["url"]
            logging.info("Obtained StreamP2P upload URL: {}".format(streamp2p_upload_url))
        else:
            logging.error("Failed to get StreamP2P upload server: {}".format(server_data.get("message") or server_data))
            return None
    except Exception as e:
        logging.error("Error getting StreamP2P upload URL: {}".format(e))
        return None

    # Step 2: Upload file to the obtained URL
    
    try:
        with open(file_path, "rb") as f:
            files = {"file": f}
            # StreamP2P API documentation suggests the API key might be in the header for the upload itself
            # or part of the URL. Given the previous step, we'll assume the obtained URL is pre-signed or handles auth.
            response = requests.post(streamp2p_upload_url, files=files, timeout=300)
            response.raise_for_status()
            try:
                upload_data = response.json()
            except json.JSONDecodeError:
                logging.error("DoodStream upload response is not valid JSON: {}".format(response.text))
                return None

            if upload_data.get("status") == "OK": # Assuming a success status
                logging.info("StreamP2P upload successful: {}".format(upload_data.get("file_code")))
                return upload_data.get("url")
            else:
                logging.error("StreamP2P upload failed: {}".format(upload_data.get("message") or upload_data))
                return None
    except Exception as e:
        logging.error("Error uploading to StreamP2P: {}".format(e))
        return None

def main(anime_name):
    logging.info("Starting anime downloader for: {}".format(anime_name))
    cleanup_disk_space()

    # --- Test with Dummy Video --- #
    if anime_name == "Test Dummy Video":
        logging.info("--- Running Test with Dummy Video ---")
        dummy_dir = "/home/ubuntu/downloads/test_anime"
        os.makedirs(dummy_dir, exist_ok=True)
        dummy_video_path = os.path.join(dummy_dir, "dummy_video.mp4")
        hardsubbed_video_path = os.path.join(dummy_dir, "dummy_video_hardsubbed.mp4")

        # Create a dummy video file if it doesn't exist
        if not os.path.exists(dummy_video_path):
            logging.info("Creating dummy video file...")
            run_command([
                "ffmpeg", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25", 
                "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=44100",
                "-t", "10", "-y", dummy_video_path
            ])

        # Create a dummy subtitle file
        dummy_sub_path = os.path.join(dummy_dir, "dummy_video.srt")
        with open(dummy_sub_path, "w") as f:
            f.write("1\n00:00:01,000 --> 00:00:03,000\nThis is a dummy subtitle.\n\n")
            f.write("2\n00:00:04,000 --> 00:00:06,000\nTesting hard-subbing functionality.\n")
        logging.info("Dummy subtitle created at: {}".format(dummy_sub_path))

        # Hard-sub the dummy video
        if hardsub_video(dummy_video_path, hardsubbed_video_path, external_subtitle_path=dummy_sub_path, video_has_embedded_subs=False):
            logging.info("Dummy video hard-subbed successfully.")
            
            # Upload to DoodStream
            doodstream_url = upload_to_doodstream(hardsubbed_video_path)
            if doodstream_url:
                logging.info("Uploaded to DoodStream: {}".format(doodstream_url))
            else:
                logging.error("Failed to upload to DoodStream.")

            # Upload to StreamP2P
            streamp2p_url = upload_to_streamp2p(hardsubbed_video_path)
            if streamp2p_url:
                logging.info("Uploaded to StreamP2P: {}".format(streamp2p_url))
            else:
                logging.error("Failed to upload to StreamP2P.")
        else:
            logging.error("Hard-subbing of dummy video failed.")

        logging.info("Finished processing for: {}".format(anime_name))
        return

    # --- Main Workflow --- #
    # (The rest of the main workflow logic will be implemented here)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        anime_name_input = sys.argv[1]
        main(anime_name_input)
    else:
        logging.error("Please provide an anime name as an argument.")
        sys.exit(1)
