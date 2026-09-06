import os
import sys
import json
import re
import time
import asyncio
import subprocess
import logging
from typing import Dict, Any, List

# Reconfigure stdout & stderr to UTF-8 on Windows for safe emoji & Devanagari logging
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("UltraFast3TierPodcastShortsPipeline")

# ==============================================================================
# ENVIRONMENT & CONFIGURATION
# ==============================================================================
def load_env_file(env_path: str = ".env"):
    """Reads .env file and sets environment variables if present."""
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))

load_env_file()

# Strictly load API Credentials from environment (.env)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN") or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
FACEBOOK_TARGET_ID = os.getenv("FACEBOOK_PAGE_ID", "").strip() or "me"

# Search Query & Podcast Settings
SEARCH_QUERY = os.getenv("SEARCH_QUERY", "hindi podcast interview")
PARTS_PER_VIDEO = int(os.getenv("PARTS_PER_VIDEO", "1"))  # Exactly 1 Best Short extracted per podcast video
DURATION_PER_SHORT = int(os.getenv("DURATION_PER_SHORT", "60"))
MIN_CLIP_START_SEC = float(os.getenv("MIN_CLIP_START_SEC", "180.0"))  # Minimum 3 minutes (180s) start offset for shorts extraction
YOUTUBE_CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
YOUTUBE_TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", "token.json")

DOWNLOAD_DIR = "downloads"
OUTPUT_DIR = "output_shorts"
QUEUE_DIR = os.path.join(OUTPUT_DIR, "queue")
TEMP_DIR = "temp"
SFX_DIR = "sfx"
TOP_VIDEO_PATH = "mk.mp4"
INTRO_HOOK_DELAY_SEC = 3.0  # Duration for mk.mp4 intro speech ("ye video pahle last tak dekho")
PROCESSED_VIDEOS_FILE = "processed_videos.json"
QUEUE_INFO_FILE = os.path.join(QUEUE_DIR, "queue_info.json")
TOPIC_STATE_FILE = "topic_state.json"

# 100% Anti-Copyright & Fair Use Settings
ENABLE_FAIRUSE_TRANSFORM = True
FAIRUSE_SPEED_FACTOR = 1.04  # 4% tempo & frame speedup to defeat Content ID audio/video fingerprint matching
FAIRUSE_WATERMARK_TEXT = "TRANSFORMATIVE FAIR USE"

# Aesthetic Video Visual Filters (Dynamic Color Grading)
VIDEO_FILTERS_LIST = [
    "eq=contrast=1.08:brightness=0.02:saturation=1.15",  # Warm Cinematic
    "eq=contrast=1.06:brightness=0.01:saturation=1.25",  # Vibrant HD
    "eq=contrast=1.10:brightness=-0.01:saturation=1.08", # Sharp Contrast
    "eq=contrast=1.05:brightness=0.03:saturation=1.10,colorbalance=rs=0.03:bs=-0.03",  # Golden Warm
    "eq=contrast=1.07:brightness=0.01:saturation=1.12,colorbalance=rs=-0.02:bs=0.04"   # Cool Studio Tech
]

def get_random_video_filter() -> str:
    """Returns a random visual aesthetic filter for the podcast video."""
    import random
    selected_filter = random.choice(VIDEO_FILTERS_LIST)
    logger.info(f"🎨 Applied Dynamic Aesthetic Video Filter: '{selected_filter}'")
    return selected_filter

# 30 Seed Human Podcast Topics
SEED_TOPICS_LIST = [
    "Human Body Podcast",
    "Human Psychology Podcast",
    "Brain Science Podcast",
    "Neuroscience Podcast",
    "Mental Health Podcast",
    "Sleep Science Podcast",
    "Dopamine & Motivation Podcast",
    "Stress & Anxiety Podcast",
    "Focus & Productivity Podcast",
    "Memory & Learning Podcast",
    "Human Behavior Podcast",
    "Emotions & Psychology Podcast",
    "Confidence & Self-Esteem Podcast",
    "Relationships & Dating Psychology Podcast",
    "Social Psychology Podcast",
    "Mindset & Self-Improvement Podcast",
    "Longevity & Healthy Aging Podcast",
    "Nutrition & Diet Podcast",
    "Fitness & Exercise Science Podcast",
    "Muscle Growth Podcast",
    "Hormones & Testosterone Podcast",
    "Weight Loss Science Podcast",
    "Heart Health Podcast",
    "Gut Health Podcast",
    "Cold Exposure & Recovery Podcast",
    "Meditation & Mindfulness Podcast",
    "Creativity & Human Performance Podcast",
    "Addiction & Habit Formation Podcast",
    "Emotional Intelligence Podcast",
    "Science of Happiness Podcast"
]

# Standard Fair Use Disclaimer against copyright strikes
FAIR_USE_DISCLAIMER = """
==================================================
⚠️ COPYRIGHT DISCLAIMER & FAIR USE NOTICE:
This video is created for educational, commentary, news, and transformative entertainment purposes. All original audio and video clips belong directly to their respective podcast creators and channels. Under Section 107 of the Copyright Act 1976, allowance is made for "Fair Use" for purposes such as criticism, comment, news reporting, teaching, scholarship, and research. Non-profit, educational, or personal use tips the balance in favor of fair use.
==================================================
"""

# ==============================================================================
# HELPER: UTILITY FUNCTIONS & HISTORY TRACKING
# ==============================================================================
def parse_json_safely(content: str) -> Dict[str, Any]:
    """Extracts and parses JSON object from LLM output safely."""
    if not content:
        return {}
    
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if json_match:
        json_str = json_match.group(0).strip()
        try:
            return json.loads(json_str)
        except Exception:
            try:
                fixed_str = re.sub(r'[\r\n]+', ' ', json_str)
                return json.loads(fixed_str)
            except Exception:
                pass
    return {}

def load_processed_videos() -> List[str]:
    """Loads list of video IDs that have already been processed into shorts."""
    if os.path.exists(PROCESSED_VIDEOS_FILE):
        try:
            with open(PROCESSED_VIDEOS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def mark_video_as_processed(video_id: str):
    """Saves a processed video ID into processed_videos.json."""
    processed = load_processed_videos()
    if video_id not in processed:
        processed.append(video_id)
        with open(PROCESSED_VIDEOS_FILE, "w", encoding="utf-8") as f:
            json.dump(processed, f, indent=2)
        logger.info(f"Video ID '{video_id}' saved to '{PROCESSED_VIDEOS_FILE}' (Total Processed: {len(processed)})")

def load_topic_state() -> Dict[str, Any]:
    """Loads current topic state from topic_state.json."""
    if os.path.exists(TOPIC_STATE_FILE):
        try:
            with open(TOPIC_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_topic_state(state: Dict[str, Any]):
    """Saves topic state into topic_state.json."""
    with open(TOPIC_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def get_next_human_topic(groq_client=None) -> str:
    """
    Retrieves the next daily topic for human health/psychology/science podcasts.
    Cycles through SEED_TOPICS_LIST sequentially. Once seed topics are exhausted,
    uses Groq LLM to dynamically generate new unique human-centric podcast topics.
    """
    state = load_topic_state()
    current_index = state.get("current_index", 0)
    used_topics = state.get("used_topics", [])

    topic = ""
    if current_index < len(SEED_TOPICS_LIST):
        topic = SEED_TOPICS_LIST[current_index]
        logger.info(f"📍 Selected Seed Topic ({current_index + 1}/{len(SEED_TOPICS_LIST)}): '{topic}'")
    else:
        # Dynamically generate AI topic using Groq LLM
        if groq_client:
            try:
                recent_used = used_topics[-30:] if used_topics else []
                prompt = (
                    "You are a podcast content strategist for viral Tier-1 US podcasts. Generate 1 fresh, highly engaging YouTube search topic for an English podcast interview. "
                    "The topic MUST be about human biology, psychology, neuroscience, health, brain science, habits, performance, or mind. "
                    f"Do NOT reuse any of these recent topics: {json.dumps(recent_used)}. "
                    "Return ONLY a JSON object: {\"topic\": \"Topic Name Podcast\"}"
                )
                response = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model="llama-3.3-70b-versatile",
                    temperature=0.7
                )
                res_text = response.choices[0].message.content or ""
                parsed = parse_json_safely(res_text)
                topic = parsed.get("topic", "").strip()
            except Exception as e:
                logger.warning(f"AI topic generation warning: {e}")

        if not topic:
            fallback_idx = current_index % len(SEED_TOPICS_LIST)
            topic = SEED_TOPICS_LIST[fallback_idx]
            logger.info(f"📍 AI topic fallback selected seed topic: '{topic}'")

    state["current_index"] = current_index + 1
    if topic not in used_topics:
        used_topics.append(topic)
    state["used_topics"] = used_topics
    state["last_used_topic"] = topic
    save_topic_state(state)

    return topic

def load_queue_info() -> Dict[str, Any]:
    """Loads queue metadata from queue_info.json."""
    if os.path.exists(QUEUE_INFO_FILE):
        try:
            with open(QUEUE_INFO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_queue_info(info: Dict[str, Any]):
    """Saves queue metadata into queue_info.json."""
    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(QUEUE_INFO_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)

def get_audio_duration(file_path: str) -> float:
    """Returns duration of an audio/video file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        out = subprocess.check_output(cmd).decode().strip()
        return float(out)
    except Exception:
        return 0.0

def get_available_sfx_files() -> List[str]:
    """Loads sound effects from sfx/ directory if present."""
    if os.path.exists(SFX_DIR):
        files = [os.path.join(SFX_DIR, f) for f in os.listdir(SFX_DIR) if f.endswith((".mp3", ".wav"))]
        return files
    return []

# ==============================================================================
# MODULE 1: MOST VIRAL HINDI PODCAST SEARCH & FALLBACK DOWNLOADER
# ==============================================================================
def fetch_and_download_most_viral_podcast(
    search_query: str = "hindi podcast interview",
    download_dir: str = "downloads",
    max_duration_sec: int = 300,
    groq_client: Any = None
) -> Dict[str, Any]:
    """
    Searches YouTube for viral Hindi podcasts for a topic, sorts by view count,
    strictly filters out ALREADY PROCESSED video IDs, and downloads candidate video.
    If current topic has no unprocessed candidates, automatically switches to next topic.
    """
    import yt_dlp
    from yt_dlp.utils import download_range_func

    processed_list = load_processed_videos()
    current_topic = search_query

    for topic_attempt in range(5):
        logger.info(f"Searching YouTube for most viral podcasts for USA/Tier-1 (Topic #{topic_attempt + 1}: '{current_topic}')...")
        
        query = f"ytsearch25:{current_topic} podcast interview"
        ydl_opts_search = {
            'extract_flat': True,
            'quiet': True,
            'socket_timeout': 30,
            'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'web']}}
        }
        
        entries_sorted = []
        for retry in range(4):
            try:
                with yt_dlp.YoutubeDL(ydl_opts_search) as ydl:
                    info = ydl.extract_info(query, download=False)
                    entries = info.get('entries', []) if info else []
                    
                    entries_with_views = []
                    for e in entries:
                        if e:
                            v_id = e.get('id')
                            v_url = e.get('url') or f"https://www.youtube.com/watch?v={v_id}"
                            v_title = e.get('title', 'Hindi Podcast')
                            v_views = e.get('view_count') or 0
                            if v_id and v_id not in processed_list:
                                entries_with_views.append({
                                    "id": v_id,
                                    "url": v_url,
                                    "title": v_title,
                                    "views": v_views
                                })
                            elif v_id:
                                logger.info(f"Skipping previously processed YouTube video ID: {v_id}")

                    if entries_with_views:
                        entries_sorted = sorted(entries_with_views, key=lambda x: x["views"], reverse=True)
                        break
            except Exception as e:
                logger.warning(f"YouTube search network retry ({retry+1}/4): {e}")
                time.sleep(3)

        if entries_sorted:
            # Try downloading top candidates until one succeeds cleanly
            for candidate in entries_sorted[:5]:
                v_url = candidate["url"]
                v_id = candidate["id"]
                v_title = candidate["title"]
                logger.info(f"Attempting download for Most Viral Candidate ({candidate['views']:,} views): '{v_title}' (ID: {v_id})...")

                for f in os.listdir(download_dir):
                    if f.startswith("input_video"):
                        try:
                            os.remove(os.path.join(download_dir, f))
                        except Exception:
                            pass

                output_template = os.path.join(download_dir, f"input_video_{v_id}.%(ext)s")

                ydl_opts_dl = {
                    'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]',
                    'outtmpl': output_template,
                    'merge_output_format': 'mp4',
                    'overwrites': True,
                    'noplaylist': True,
                    'quiet': False,
                    'socket_timeout': 30,
                    'retries': 10,
                    'fragment_retries': 10,
                    'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'web']}},
                    'download_ranges': download_range_func(None, [(0, max_duration_sec)]),
                    'force_keyframes_at_cuts': True,
                    'concurrent_fragment_downloads': 8,
                }

                try:
                    with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
                        info_dict = ydl.extract_info(v_url, download=True)
                        
                        expected_file = None
                        for file in os.listdir(download_dir):
                            if file.startswith("input_video") and not file.endswith(".part"):
                                expected_file = os.path.join(download_dir, file)
                                break

                        if expected_file and os.path.exists(expected_file) and os.path.getsize(expected_file) > 1000000:
                            logger.info(f"Download Success! '{v_title}' -> {expected_file}")
                            return {
                                "file_path": os.path.abspath(expected_file),
                                "title": v_title,
                                "id": v_id,
                                "duration": float(max_duration_sec),
                                "topic": current_topic
                            }
                except Exception as e:
                    logger.warning(f"Download candidate '{v_title}' failed ({e}). Trying next candidate...")

        # If current topic produced no unprocessed video candidates or all failed, pick next topic!
        logger.warning(f"No unprocessed videos found for topic '{current_topic}'. Switching to next daily topic...")
        current_topic = get_next_human_topic(groq_client=groq_client)

    raise RuntimeError("All video download candidates failed across all attempted topics!")

# ==============================================================================
# MODULE 2: AI TRANSCRIPT TOPIC & VIRAL HIGHLIGHT SELECTION (LLM Content Analysis)
# ==============================================================================
def find_ai_viral_highlight_timestamps(
    groq_client: Any,
    video_path: str,
    total_duration: float,
    num_windows: int = 1,
    window_size: float = 60.0
) -> List[float]:
    """
    Transcribes podcast dialogue via Groq Whisper and uses Groq LLM topic intelligence to analyze
    the actual conversation content and select the single BEST viral story/highlight moment.
    Enforces minimum start timestamp offset (MIN_CLIP_START_SEC = 180s / 3 mins).
    """
    logger.info(f"Analyzing AI Transcript Content Intelligence across {total_duration:.1f}s podcast (starting after {MIN_CLIP_START_SEC:.0f}s / 3 mins)...")

    # Extract audio sample starting AFTER 3 minutes (180.0s) for AI transcript analysis
    sample_start = min(MIN_CLIP_START_SEC, max(0.0, total_duration - window_size))
    sample_duration = min(300.0, max(60.0, total_duration - sample_start))
    sample_audio_path = os.path.join(TEMP_DIR, "analysis_sample.mp3")
    extract_original_audio_clip(video_path, start_sec=sample_start, duration_sec=sample_duration, output_audio_path=sample_audio_path)

    try:
        with open(sample_audio_path, "rb") as file:
            transcription = groq_client.audio.transcriptions.create(
                file=(os.path.basename(sample_audio_path), file.read()),
                model="whisper-large-v3",
                response_format="verbose_json",
                timestamp_granularities=["segment"]
            )

        segments = getattr(transcription, "segments", []) or []
        if isinstance(transcription, dict):
            segments = transcription.get("segments") or []

        timed_transcript_lines = []
        for seg in segments:
            seg_start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", 0.0)
            actual_seg_start = sample_start + seg_start
            seg_text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
            seg_text = seg_text.strip()
            if seg_text:
                timed_transcript_lines.append(f"[{actual_seg_start:.1f}s]: {seg_text}")

        if timed_transcript_lines:
            full_transcript_str = "\n".join(timed_transcript_lines[:60])
            prompt = f"""
You are a world-class viral YouTube Shorts editor.
Read the following podcast transcript with timestamps:

{full_transcript_str}

Find the single MOST INTERESTING, VIRAL, ENGAGING, OR INSPIRATIONAL conversation story/topic moment ({window_size} seconds duration).
CRITICAL RULE: The start_sec MUST be at least {MIN_CLIP_START_SEC:.1f} (after 3 minutes into the podcast).

Return ONLY a valid JSON object matching this exact schema:
{{
  "start_sec": 210.0,
  "reason": "Short 1-sentence reason why this story/topic is viral"
}}
"""
            candidate_models = ["openai/gpt-oss-120b", "groq/compound-mini", "groq/compound"]
            for model in candidate_models:
                try:
                    res = groq_client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3
                    )
                    content = res.choices[0].message.content.strip()
                    parsed = parse_json_safely(content)
                    if parsed and "start_sec" in parsed:
                        ai_start = float(parsed["start_sec"])
                        max_start = max(MIN_CLIP_START_SEC, total_duration - window_size)
                        clamped_start = max(MIN_CLIP_START_SEC, min(ai_start, max_start))
                        reason = parsed.get("reason", "Top viral conversation moment selected by AI")
                        logger.info(f"AI Selected Top Viral Topic Timestamp: {clamped_start:.1f}s (Reason: {reason})")
                        return [clamped_start][:num_windows]
                except Exception as e:
                    logger.warning(f"AI Transcript highlight detection retry with '{model}' failed: {e}")

    except Exception as e:
        logger.warning(f"AI Transcript analysis exception ({e}). Falling back to default start timestamp...")

    fallback_start = min(MIN_CLIP_START_SEC, max(0.0, total_duration - window_size))
    logger.info(f"Fallback: Selected default start timestamp [{fallback_start:.1f}s] (after 3 mins)")
    return [fallback_start][:num_windows]

# ==============================================================================
# MODULE 3: ORIGINAL AUDIO EXTRACTION & SEO GENERATION
# ==============================================================================
def extract_original_audio_clip(video_path: str, start_sec: float, duration_sec: float, output_audio_path: str):
    """
    Extracts original audio clip from video segment for Groq Whisper transcription.
    """
    logger.info(f"Extracting podcast audio segment ({start_sec}s - {start_sec + duration_sec}s)...")
    os.makedirs(os.path.dirname(output_audio_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-t", str(duration_sec),
        "-i", video_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "4",
        output_audio_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def generate_seo_metadata(groq_client: Any, video_title: str) -> Dict[str, Any]:
    """
    Generates viral US/Tier-1 title, description, and hashtags/tags for YouTube Shorts & Facebook Reels.
    """
    logger.info(f"Generating viral Tier-1 US SEO metadata & hashtags for '{video_title}'...")

    prompt = f"""
You are an expert Social Media Manager for viral US Podcast YouTube Shorts & Instagram Reels targeting Tier-1 audiences (USA, UK, Canada).
Podcast Title: "{video_title}"

Generate high-CTR, curiosity-gap viral SEO metadata strictly in American English.
Title must be dramatic, catchy, or thought-provoking (under 70 chars).
Description should be a compelling 2-sentence English summary with strong hook and call-to-action.
Include top Tier-1 viral hashtags: #neuroscience #psychology #brainhealth #mindset #productivity #shorts #reels #viral #usa #podcast.

Respond ONLY with a valid JSON object matching this exact schema:
{{
  "title": "Unbelievable Brain Secret That Changes Everything 🧠 #Shorts #Reels",
  "description": "Discover how top scientists explain the hidden key behind focus, motivation, and neuroplasticity. Watch till the end!",
  "tags": ["neuroscience", "psychology", "brainhealth", "mindset", "productivity", "shorts", "reels", "viral", "usa", "podcast"]
}}
"""

    candidate_models = ["groq/compound", "openai/gpt-oss-120b", "groq/compound-mini"]
    for model in candidate_models:
        try:
            res = groq_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            content = res.choices[0].message.content.strip()
            parsed = parse_json_safely(content)
            if parsed and "title" in parsed and "description" in parsed and "tags" in parsed:
                logger.info(f"SEO Metadata generated successfully using model '{model}'")
                return parsed
        except Exception as e:
            logger.warning(f"SEO Metadata generation failed with model '{model}': {e}")

    return {
        "title": f"{video_title} 🎬 #Shorts #Reels",
        "description": f"Watch this mind-blowing podcast breakdown on {video_title} #Shorts #Viral #USA",
        "tags": ["neuroscience", "psychology", "brainhealth", "mindset", "productivity", "shorts", "reels", "viral", "usa", "podcast"]
    }

# ==============================================================================
# MODULE 4: ULTRA-FAST BATCH ENGLISH SUBTITLE GENERATION (Real-Time Voice Sync + ASS Animation!)
# ==============================================================================
def batch_convert_to_english(groq_client: Any, text_list: List[str]) -> List[str]:
    """
    Converts/translates a batch list of transcript sentences into clean 100% English UPPERCASE captions
    for USA/Tier-1 viewers using 1 single Groq API call.
    """
    if not text_list:
        return []

    indexed_lines = "\n".join([f"{idx+1}. {t}" for idx, t in enumerate(text_list)])
    
    prompt = f"""
Convert/translate the following numbered list of transcript sentences into clean, fluent 100% English.
If input sentences are in Hindi or Devanagari, translate them accurately to English.
If input sentences are already in English, refine them for clarity and impact.

Input Sentences:
{indexed_lines}

Respond ONLY with a numbered list of converted English sentences in UPPERCASE, matching the exact item count:
1. ...
2. ...
"""
    candidate_models = ["groq/compound", "openai/gpt-oss-120b", "groq/compound-mini"]
    for model in candidate_models:
        try:
            res = groq_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            content = res.choices[0].message.content.strip()
            converted = []
            for line in content.split("\n"):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith(tuple("0123456789"))):
                    cleaned = re.sub(r'^\d+[\.\)]\s*', '', line).strip().upper()
                    if cleaned:
                        converted.append(cleaned)
            
            if len(converted) == len(text_list):
                return converted
        except Exception as e:
            logger.warning(f"Batch English subtitle conversion failed with model '{model}': {e}")

    return [t.upper() for t in text_list]

def format_ass_timestamp(seconds: float) -> str:
    """Converts seconds into ASS time format: H:MM:SS.cs (centiseconds)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        secs += 1
        centis -= 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

def generate_ass_subtitles(groq_client: Any, audio_mp3_path: str, output_ass_path: str, delay_sec: float = 3.0):
    """
    Uses Groq's whisper-large-v3 API with word-level timestamps (timestamp_granularities=["word", "segment"]),
    batch-converts sentences into Hinglish, maps exact real-time word audio timings, and generates
    high-CTR animated Pop-up ASS subtitles (1-2 words per line) shifted by delay_sec.
    """
    logger.info(f"Transcribing podcast voice with Groq whisper-large-v3 API (Word-level Real-Time Sync, Subtitle Delay={delay_sec}s)...")
    os.makedirs(os.path.dirname(output_ass_path), exist_ok=True)

    with open(audio_mp3_path, "rb") as file:
        transcription = groq_client.audio.transcriptions.create(
            file=(os.path.basename(audio_mp3_path), file.read()),
            model="whisper-large-v3",
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"]
        )

    words_raw = getattr(transcription, "words", []) or []
    if isinstance(transcription, dict):
        words_raw = transcription.get("words") or []

    segments = getattr(transcription, "segments", []) or []
    if isinstance(transcription, dict):
        segments = transcription.get("segments") or []

    valid_segments = []
    hindi_texts = []

    for seg in segments:
        seg_text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
        seg_start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", 0.0)
        seg_end = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", 0.0)

        seg_text = seg_text.strip()
        if seg_text:
            valid_segments.append({"start": seg_start, "end": seg_end, "text": seg_text})
            hindi_texts.append(seg_text)

    logger.info(f"Batch converting {len(hindi_texts)} subtitle lines to English in 1 LLM request...")
    english_texts = batch_convert_to_english(groq_client, hindi_texts)

    ass_events = []

    if words_raw:
        logger.info(f"Processing {len(words_raw)} real-time word timestamps with English mapping...")
        word_list = []
        for w in words_raw:
            w_text = w.get("word") if isinstance(w, dict) else getattr(w, "word", "")
            w_start = w.get("start") if isinstance(w, dict) else getattr(w, "start", 0.0)
            w_end = w.get("end") if isinstance(w, dict) else getattr(w, "end", 0.0)
            w_text = w_text.strip()
            if w_text:
                word_list.append({"text": w_text, "start": w_start, "end": w_end})

        all_english_words = []
        for eng_text in english_texts:
            all_english_words.extend(eng_text.split())

        chunk = []
        for i, ew in enumerate(all_english_words):
            if i < len(word_list):
                w_info = word_list[i]
                start_t = w_info["start"] + delay_sec
                end_t = max(w_info["end"] + delay_sec, start_t + 0.3)
            else:
                start_t = (chunk[-1]["end"] if chunk else delay_sec)
                end_t = start_t + 0.3

            chunk.append({"text": ew, "start": start_t, "end": end_t})

            if len(chunk) >= 2 or i == len(all_english_words) - 1:
                c_start = chunk[0]["start"]
                c_end = chunk[-1]["end"]
                c_text = " ".join([item["text"] for item in chunk])
                ass_events.append({"start": c_start, "end": c_end, "text": c_text})
                chunk = []
    else:
        logger.info("Falling back to segment word timing...")
        for seg, eng_text in zip(valid_segments, english_texts):
            words = eng_text.split()
            if not words:
                continue

            seg_start = seg["start"]
            seg_end = seg["end"]
            seg_dur = max(0.5, seg_end - seg_start)
            word_dur = seg_dur / len(words)

            chunk = []
            for w_idx, w in enumerate(words):
                if not chunk:
                    chunk_start = seg_start + (w_idx * word_dur) + delay_sec

                chunk.append(w)
                chunk_end = chunk_start + (len(chunk) * word_dur)

                if len(chunk) >= 2 or w_idx == len(words) - 1:
                    ass_events.append({
                        "start": chunk_start,
                        "end": chunk_end,
                        "text": " ".join(chunk)
                    })
                    chunk = []

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,76,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,40,40,310,1
Style: FairUseWatermark,Arial,34,&H80FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,7,30,30,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        if ENABLE_FAIRUSE_TRANSFORM:
            f.write(f"Dialogue: 0,0:00:00.00,0:10:00.00,FairUseWatermark,,0,0,0,,{FAIRUSE_WATERMARK_TEXT}\n")
        for entry in ass_events:
            s_str = format_ass_timestamp(entry["start"])
            e_str = format_ass_timestamp(entry["end"])
            animated_text = f"{{\\fscx120\\fscy120\\t(0,80,\\fscx100\\fscy100)}}{entry['text']}"
            f.write(f"Dialogue: 0,{s_str},{e_str},Default,,0,0,0,,{animated_text}\n")

    logger.info(f"Real-Time Voice-Synced ASS Animated Subtitles saved to: {output_ass_path} ({len(ass_events)} entries)")

def generate_srt_subtitles(groq_client: Any, audio_mp3_path: str, output_srt_path: str, delay_sec: float = 3.0):
    """Backward compatible wrapper calling generate_ass_subtitles."""
    ass_path = output_srt_path.replace(".srt", ".ass")
    generate_ass_subtitles(groq_client, audio_mp3_path, ass_path, delay_sec)
    return ass_path

# ==============================================================================
# MODULE 5: ULTRA-FAST SEQUENTIAL 3-TIER CANVAS RENDERING (-preset superfast)
# ==============================================================================
def render_short_with_original_audio(
    input_video_path: str,
    srt_path: str,
    output_video_path: str,
    start_sec: float,
    duration_sec: float,
    groq_client: Any = None,
    bgm_path: str = "bgm.mp3",
    top_video_path: str = "mk.mp4",
    outro_video_path: str = "mk2.mp4",
    intro_delay_sec: float = INTRO_HOOK_DELAY_SEC
):
    """
    Renders 9:16 vertical short matching sequential intro hook workflow in ~30 SECONDS (-preset superfast):
    1. FIRST: Top 'mk.mp4' plays speaking ("ye video pahle last tak dekho") for intro_delay_sec (3.0s).
    2. During first 3.0s, main podcast video at center is FROZEN on frame 1.
    3. AFTER 3.0s: Main podcast video & voice start playing!
    4. Real-time animated Pop-up Hinglish captions appear in bottom black bar space.
    5. FINALLY: Appends full-screen outro video 'mk2.mp4' seamlessly at the very end after main clip finishes!
    """
    logger.info(f"Rendering ULTRA-FAST 3-TIER CANVAS short (Intro Delay={intro_delay_sec}s, Outro Video={outro_video_path}) -> {output_video_path}...")
    os.makedirs(os.path.dirname(output_video_path), exist_ok=True)

    use_top_video = os.path.exists(top_video_path)
    use_bgm = os.path.exists(bgm_path)
    use_outro_video = os.path.exists(outro_video_path)
    sfx_files = get_available_sfx_files()
    use_sfx = len(sfx_files) > 0

    escaped_sub = srt_path.replace("\\", "/").replace(":", "\\:")

    if srt_path.endswith(".ass"):
        subtitles_filter = f"subtitles='{escaped_sub}'"
    else:
        subtitles_filter = (
            f"subtitles='{escaped_sub}':force_style="
            f"'Fontname=Arial,Fontsize=22,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,"
            f"BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=45'"
        )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-t", str(duration_sec),
        "-i", input_video_path  # Input 0: Main Podcast Video
    ]

    inputs_count = 1

    if use_bgm:
        cmd.extend(["-stream_loop", "-1", "-i", bgm_path])  # Input 1: BGM
        bgm_idx = inputs_count
        inputs_count += 1

    if use_top_video:
        cmd.extend(["-stream_loop", "-1", "-i", top_video_path])  # Input 2: Top mk.mp4
        top_v_idx = inputs_count
        inputs_count += 1

    if use_sfx:
        whoosh_sfx = next((f for f in sfx_files if "whoosh" in os.path.basename(f).lower()), sfx_files[0])
        cmd.extend(["-i", whoosh_sfx])  # Input 3: SFX
        sfx_idx = inputs_count
        inputs_count += 1

    if use_outro_video:
        cmd.extend(["-i", outro_video_path])  # Input 4: Outro mk2.mp4
        outro_v_idx = inputs_count
        inputs_count += 1

    filter_parts = []
    main_dur = duration_sec + (intro_delay_sec if use_top_video else 0.0)
    
    # 1. Video Canvas Construction & Anti-Copyright Transform
    active_color_filter = get_random_video_filter() if ENABLE_FAIRUSE_TRANSFORM else "eq=contrast=1.0:brightness=0.0:saturation=1.0"

    if use_top_video:
        if ENABLE_FAIRUSE_TRANSFORM:
            filter_parts.append(f"[0:v]{active_color_filter},setpts=PTS/{FAIRUSE_SPEED_FACTOR},scale=1080:608:force_original_aspect_ratio=decrease,tpad=start_mode=clone:start_duration={intro_delay_sec}[mainv]")
        else:
            filter_parts.append(f"[0:v]scale=1080:608:force_original_aspect_ratio=decrease,tpad=start_mode=clone:start_duration={intro_delay_sec}[mainv]")

        filter_parts.append(f"[{top_v_idx}:v]scale=1080:608:force_original_aspect_ratio=decrease[topv]")
        filter_parts.append(f"color=c=black:s=1080x1920:d={main_dur}[bg]")
        filter_parts.append(f"[bg][topv]overlay=0:24[bg1]")
        filter_parts.append(f"[bg1][mainv]overlay=0:656[bg2]")
        if use_outro_video:
            filter_parts.append(f"[bg2]{subtitles_filter},trim=duration={main_dur},setpts=PTS-STARTPTS,fps=30[v_main]")
        else:
            filter_parts.append(f"[bg2]{subtitles_filter}[v]")
    else:
        if ENABLE_FAIRUSE_TRANSFORM:
            letterbox_filter = f"{active_color_filter},setpts=PTS/{FAIRUSE_SPEED_FACTOR},scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        else:
            letterbox_filter = "scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"

        if use_outro_video:
            filter_parts.append(f"[0:v]{letterbox_filter},{subtitles_filter},trim=duration={main_dur},setpts=PTS-STARTPTS,fps=30[v_main]")
        else:
            filter_parts.append(f"[0:v]{letterbox_filter},{subtitles_filter}[v]")

    # 2. Audio Mix Construction & Audio Fingerprint Bypass
    audio_mix_str = ""
    mix_inputs = []

    if use_top_video:
        audio_mix_str += f"[{top_v_idx}:a]volume=1.0[topa];"
        mix_inputs.append("[topa]")

    delay_ms = int(intro_delay_sec * 1000)
    if ENABLE_FAIRUSE_TRANSFORM:
        audio_mix_str += f"[0:a]atempo={FAIRUSE_SPEED_FACTOR},asetrate=44100*1.01,aresample=44100,adelay={delay_ms}|{delay_ms},volume=1.0[podca];"
    else:
        audio_mix_str += f"[0:a]adelay={delay_ms}|{delay_ms},volume=1.0[podca];"

    mix_inputs.append("[podca]")

    if use_bgm:
        audio_mix_str += f"[{bgm_idx}:a]volume=0.15[bgm];"
        mix_inputs.append("[bgm]")

    if use_sfx:
        sfx_delay_ms = delay_ms + 15000
        audio_mix_str += f"[{sfx_idx}:a]adelay={sfx_delay_ms}|{sfx_delay_ms},volume=0.6[sfx];"
        mix_inputs.append("[sfx]")

    mix_count = len(mix_inputs)
    if use_outro_video:
        audio_mix_str += "".join(mix_inputs) + f"amix=inputs={mix_count}:duration=first:dropout_transition=2,atrim=duration={main_dur},asetpts=PTS-STARTPTS,aformat=sample_rates=44100:channel_layouts=stereo[a_main]"
    else:
        audio_mix_str += "".join(mix_inputs) + f"amix=inputs={mix_count}:duration=first:dropout_transition=2[a]"
    filter_parts.append(audio_mix_str)

    # 3. Outro Video Concatenation
    if use_outro_video:
        filter_parts.append(f"[{outro_v_idx}:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30[v_outro]")
        filter_parts.append(f"[{outro_v_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo[a_outro]")
        filter_parts.append("[v_main][a_main][v_outro][a_outro]concat=n=2:v=1:a=1[v][a]")

    filter_complex = ";".join(filter_parts)

    cmd_args = [
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "superfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k"
    ]
    if not use_outro_video:
        cmd_args.extend(["-t", str(main_dur)])
    cmd_args.append(output_video_path)

    cmd.extend(cmd_args)

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info(f"Rendered ULTRA-FAST 3-TIER CANVAS short successfully (TopVideo={use_top_video}, SFX={use_sfx}): {output_video_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg rendering error: {e.stderr.decode('utf-8', errors='ignore')}")
        raise e

# ==============================================================================
# MODULE 6: FACEBOOK & YOUTUBE AUTO-UPLOAD
# ==============================================================================
def upload_short_to_facebook(
    video_path: str,
    title: str,
    description: str,
    tags: List[str],
    fair_use_disclaimer: str,
    target_id: str,
    access_token: str
) -> bool:
    """
    Attempts auto-upload to Facebook Account (Professional Mode Profile or Page) using Graph API.
    """
    logger.info("Checking Facebook API credentials for upload...")

    if not access_token:
        logger.warning(
            "[INFO] Facebook credentials missing ('FACEBOOK_ACCESS_TOKEN' not set in .env). "
            f"Skipping Facebook upload. Video saved locally in {OUTPUT_DIR}/"
        )
        return False

    if not target_id or target_id.strip() == "":
        target_id = "me"

    hashtags_str = " ".join([f"#{t.replace(' ', '')}" for t in tags])
    full_description = f"{title}\n\n{description}\n\n{hashtags_str}\n\n{fair_use_disclaimer}"

    url = f"https://graph.facebook.com/v19.0/{target_id}/videos"

    try:
        import requests
        profile_label = "Facebook Personal Profile (Professional Mode)" if target_id == "me" else f"Facebook Page ({target_id})"
        logger.info(f"Uploading '{title}' to {profile_label}...")
        
        with open(video_path, "rb") as video_file:
            payload = {
                "access_token": access_token,
                "title": title[:100],
                "description": full_description
            }
            files = {
                "source": (os.path.basename(video_path), video_file, "video/mp4")
            }
            response = requests.post(url, data=payload, files=files, timeout=600)
            res_data = response.json()
            if response.status_code == 200 and "id" in res_data:
                logger.info(f"Facebook Upload Successful! Reel/Video ID: {res_data.get('id')}")
                return True
            else:
                logger.warning(f"[INFO] Facebook API upload response: {res_data}")
                return False
    except Exception as e:
        logger.warning(f"[INFO] Facebook API upload failed ({e}). Skipping Facebook upload.")
        return False

def upload_short_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: List[str],
    fair_use_disclaimer: str,
    client_secrets_file: str,
    token_file: str
) -> bool:
    """
    Attempts auto-upload to YouTube via OAuth2 API.
    """
    logger.info("Checking YouTube API credentials for upload...")

    if not os.path.exists(client_secrets_file) and not os.path.exists(token_file):
        logger.warning(
            f"[INFO] YouTube credentials missing ('{client_secrets_file}' not found). "
            f"Skipping YouTube upload. Video saved locally in {OUTPUT_DIR}/"
        )
        return False

    hashtags_str = " ".join([f"#{t.replace(' ', '')}" for t in tags])
    full_description = f"{description}\n\n{hashtags_str}\n\n{fair_use_disclaimer}"

    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
        creds = None

        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif os.path.exists(client_secrets_file):
                flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
                creds = flow.run_local_server(port=0)
                with open(token_file, "w") as token:
                    token.write(creds.to_json())
            else:
                logger.warning(
                    f"[INFO] YouTube credentials invalid and '{client_secrets_file}' missing. "
                    f"Skipping YouTube upload. Video saved locally in {OUTPUT_DIR}/"
                )
                return False

        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title[:100],
                "description": full_description[:5000],
                "tags": tags,
                "categoryId": "24"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        logger.info(f"Uploading '{title}' to YouTube...")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")

        logger.info(f"YouTube Upload Successful! Video ID: {response.get('id')}")
        return True

    except Exception as e:
        logger.warning(
            f"[INFO] YouTube upload failed ({e}). Skipping YouTube upload. "
            f"Video saved locally in {OUTPUT_DIR}/"
        )
        return False

# ==============================================================================
# MAIN PIPELINE WORKFLOW (Ultra-Fast 15-Second Rendering Speed!)
# ==============================================================================
def main():
    logger.info("Starting Ultra-Fast Sequential 3-Tier Podcast Shorts Pipeline...")

    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY environment variable is missing! Please set GROQ_API_KEY in your .env file or environment.")
        sys.exit(1)

    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY)

    for folder in [DOWNLOAD_DIR, OUTPUT_DIR, QUEUE_DIR, TEMP_DIR, SFX_DIR]:
        os.makedirs(folder, exist_ok=True)

    queue_data = load_queue_info()
    queued_parts = queue_data.get("parts", [])

    valid_queued_parts = [
        p for p in queued_parts
        if os.path.exists(os.path.join(QUEUE_DIR, p.get("file_name", "")))
    ]

    # IF QUEUE IS EMPTY: Search YouTube for most viral unprocessed podcast, find highest engagement peaks, and render 2 parts!
    if not valid_queued_parts:
        logger.info("Local Short Queue is empty! Searching for most viral unprocessed Hindi podcast on YouTube...")
        
        # 1. Fetch next daily human podcast topic
        daily_topic = get_next_human_topic(groq_client=groq_client)
        logger.info(f"🎯 TODAY'S PODCAST TOPIC: '{daily_topic}'")

        # 2. Fetch & Download #1 most viral unprocessed podcast with automatic fallback retry across top candidates
        video_data = fetch_and_download_most_viral_podcast(
            search_query=daily_topic,
            download_dir=DOWNLOAD_DIR,
            max_duration_sec=300,
            groq_client=groq_client
        )
        input_video_path = video_data["file_path"]
        video_title = video_data["title"]
        video_id = video_data["id"]
        total_video_duration = video_data["duration"] or 300.0

        # 2. Analyze AI Transcript Dialogue & Select Single BEST Viral Topic Highlight!
        peak_timestamps = find_ai_viral_highlight_timestamps(
            groq_client=groq_client,
            video_path=input_video_path,
            total_duration=total_video_duration,
            num_windows=PARTS_PER_VIDEO,
            window_size=DURATION_PER_SHORT
        )

        parts_queue = []
        for p_idx, start_sec in enumerate(peak_timestamps, 1):
            part_filename = f"Part_{p_idx}.mp4"
            part_filepath = os.path.join(QUEUE_DIR, part_filename)

            logger.info(f"\n--- RENDERING ULTRA-FAST SHORT PART {p_idx}/{len(peak_timestamps)} (Peak Timestamp: {start_sec:.1f}s - {start_sec + DURATION_PER_SHORT:.1f}s) ---")
            
            # Extract Audio & Generate Hinglish Subtitles (shifted by INTRO_HOOK_DELAY_SEC)
            orig_audio_mp3 = os.path.join(TEMP_DIR, f"orig_audio_p{p_idx}.mp3")
            extract_original_audio_clip(input_video_path, start_sec, DURATION_PER_SHORT, orig_audio_mp3)

            seo_data = generate_seo_metadata(groq_client, f"{video_title} Part {p_idx}")
            ass_subtitles = os.path.join(TEMP_DIR, f"subtitles_p{p_idx}.ass")
            generate_ass_subtitles(groq_client, orig_audio_mp3, ass_subtitles, delay_sec=INTRO_HOOK_DELAY_SEC)

            # Render Ultra-Fast Sequential 3-Tier Short (mk.mp4 intro first, Podcast video starts after INTRO_HOOK_DELAY_SEC)
            render_short_with_original_audio(
                input_video_path=input_video_path,
                srt_path=ass_subtitles,
                output_video_path=part_filepath,
                start_sec=start_sec,
                duration_sec=DURATION_PER_SHORT,
                groq_client=groq_client,
                bgm_path="bgm.mp3",
                top_video_path=TOP_VIDEO_PATH,
                intro_delay_sec=INTRO_HOOK_DELAY_SEC
            )

            parts_queue.append({
                "part_num": p_idx,
                "file_name": part_filename,
                "title": seo_data.get("title", f"{video_title} Part {p_idx} 🎬 #Shorts #Reels"),
                "description": seo_data.get("description", f"Watch {video_title} Part {p_idx} #HindiPodcast #Viral"),
                "tags": seo_data.get("tags", ["hindipodcast", "podcastclips", "viral", "shorts", "reels", "trending"])
            })

        # Save video ID as processed
        mark_video_as_processed(video_id)

        # Save queue info
        queue_data = {
            "source_video_id": video_id,
            "source_video_title": video_title,
            "parts": parts_queue
        }
        save_queue_info(queue_data)
        valid_queued_parts = parts_queue
        logger.info(f"Queue filled successfully with {len(parts_queue)} Sequential 3-Tier Short parts!")

    # 4. DAILY SINGLE UPLOADER WORKFLOW (Upload ONLY 1 Part per day, then delete from queue)
    if valid_queued_parts:
        current_part = valid_queued_parts[0]
        part_num = current_part.get("part_num", 1)
        file_name = current_part.get("file_name", "Part_1.mp4")
        video_path = os.path.join(QUEUE_DIR, file_name)
        short_title = current_part.get("title", "Viral Hindi Podcast #Shorts #Reels")
        short_desc = current_part.get("description", "")
        short_tags = current_part.get("tags", ["hindipodcast", "shorts", "reels"])

        logger.info(f"\n=================== DAILY UPLOADING: {file_name} ===================")
        logger.info(f"Title: {short_title}")

        # Upload to YouTube
        yt_success = upload_short_to_youtube(
            video_path=video_path,
            title=short_title,
            description=short_desc,
            tags=short_tags,
            fair_use_disclaimer=FAIR_USE_DISCLAIMER,
            client_secrets_file=YOUTUBE_CLIENT_SECRETS_FILE,
            token_file=YOUTUBE_TOKEN_FILE
        )

        # Upload to Facebook
        fb_success = upload_short_to_facebook(
            video_path=video_path,
            title=short_title,
            description=short_desc,
            tags=short_tags,
            fair_use_disclaimer=FAIR_USE_DISCLAIMER,
            target_id=FACEBOOK_TARGET_ID,
            access_token=FACEBOOK_ACCESS_TOKEN
        )

        # Delete uploaded part file from queue & update queue_info.json ONLY if at least one upload succeeded
        if yt_success or fb_success:
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
                    logger.info(f"Deleted uploaded file '{file_name}' from local queue directory '{QUEUE_DIR}'.")
            except Exception as e:
                logger.warning(f"Could not delete '{file_name}': {e}")

            # Update remaining queue list
            remaining_parts = [p for p in valid_queued_parts if p.get("file_name") != file_name]
            queue_data["parts"] = remaining_parts
            save_queue_info(queue_data)
            logger.info(f"\nDaily Upload Finished! Successfully processed 1 short. Remaining in queue: {len(remaining_parts)} video(s).")
        else:
            logger.warning(f"\n[INFO] Neither YouTube nor Facebook upload succeeded (or credentials missing). Retaining '{file_name}' safely in local queue '{QUEUE_DIR}'.")

if __name__ == "__main__":
    main()
