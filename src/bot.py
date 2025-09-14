import discord
from discord.ext import commands, tasks
import yt_dlp as youtube_dl
from asyncio import Queue
import re
import random
import asyncio
import requests
import os
from pathlib import Path


# --- NEW: stdlib imports used by fingerprinting ---
import tempfile
import shutil
import subprocess
import json
from contextlib import contextmanager
from typing import Optional, Tuple, Dict, Any

# ——— CONFIG ———
<<<<<<< HEAD:app.py
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
# NEW: AcoustID client key for audio fingerprinting (https://acoustid.org/api-key)
ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY")
# NEW: clip length (seconds) for fingerprinting; we’ll seek into the track (not from 0s)
FINGERPRINT_CLIP_SECONDS = int(os.getenv("FP_CLIP_SECONDS", "28"))
=======
def get_token():
    t = os.getenv("DISCORD_TOKEN")
    tf = os.getenv("DISCORD_TOKEN_FILE")
    if not t and tf and Path(tf).exists():
        t = Path(tf).read_text().strip()
    return t

def get_lastfm_key():
    key = os.getenv("LASTFM_API_KEY")
    if not key:
        f = os.getenv("LASTFM_API_KEY_FILE")
        if f and Path(f).exists():
            key = Path(f).read_text().strip()
    return key

LASTFM_API_KEY = get_lastfm_key()
>>>>>>> 09901dcb989595e637a3108c9b823d17e03f968f:src/bot.py

# ——— Bot & FFmpeg/YT-DLP setup ———
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command = None)

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}
ytdl_opts = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'noplaylist': True,
}
ytdl_search_opts = {
    'default_search': 'ytsearch',
    'max_downloads': 1,
    'format': 'bestaudio/best',
}

ytdl       = youtube_dl.YoutubeDL(ytdl_opts)
ytdl_search= youtube_dl.YoutubeDL(ytdl_search_opts)

# ——— Helpers ———

def split_artist_track(full_title: str):
    """
    Split "Artist Name - Track Title" into (artist, track).
    If no dash present, artist="" and track=full_title.
    """
    parts = full_title.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", full_title.strip()

# NEW: strip common YouTube noise from titles before heuristic parsing
def normalize_title_noise(s: str) -> str:
    """Strip common YouTube noise tokens from a title."""
    s = re.sub(r"\s*\[[^\]]*\]\s*", " ", s)     # [Official Video], [Lyrics]
    s = re.sub(r"\s*\([^\)]*\)\s*", " ", s)     # (Official Audio), (HD)
    s = re.sub(r"(?i)\b(official video|official audio|lyrics|hd|4k|remastered|mv|music video)\b", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -_|·–— ").strip()

# CHANGED: HTTPS + tighter guards
def get_similar_lastfm(artist: str, track: str, limit: int = 5):
    """
    Call Last.fm track.getSimilar to return up to `limit` similar tracks.
    Returns list of dicts: {'artist': artist, 'name': track_name}.
    """
    print("Artist:" + artist, "track: "+track)
    if not LASTFM_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                'method': 'track.getSimilar',
                'artist': artist,
                'track': track,
                'api_key': LASTFM_API_KEY,
                'format': 'json',
                'limit': max(1, min(50, int(limit))),
                'autocorrect': 1
            },
            timeout=7
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    entries = data.get("similartracks", {}).get("track", [])
    results = []
    for t in entries:
        name = t.get("name")
        art  = (t.get("artist") or {}).get("name")
        if name and art:
            results.append({'artist': art.strip(), 'name': name.strip()})
    return results

def search_youtube(query: str):
    info = ytdl_search.extract_info(query, download=False)
    if 'entries' in info:
        return info['entries'][0]['webpage_url']
    return info.get('webpage_url')

def get_youtube_audio(url: str):
    info = ytdl.extract_info(url, download=False)
    for f in info.get("formats", []):
        if f.get("acodec") != "none":
            return f["url"]
    return None

def is_youtube_url(q: str):
    return re.match(r"(https?://)?(www\.)?(youtube|youtu|youtube\-nocookie)\.(com|be)/.+", q, re.IGNORECASE)

# --- NEW: fingerprinting utilities (reuse the current stream; do NOT redownload with yt-dlp) ---

@contextmanager
def _tempfile(suffix: str = ""):
    path = tempfile.mktemp(prefix="fp_", suffix=suffix)
    try:
        yield path
    finally:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

def _choose_fingerprint_window(total_duration: Optional[int], clip_len: int) -> int:
    """
    Pick a start offset that avoids very beginning (often silence/skits).
    Strategy:
      - If duration known: start at max(30s, 25% of track), but keep 5s headroom.
      - Else: default to 60s.
    """
    if total_duration and total_duration > (clip_len + 10):
        start = max(30, int(total_duration * 0.25))
        # keep some tail room
        max_start = max(0, total_duration - clip_len - 5)
        return min(start, max_start)
    return 60

def _extract_clip_from_stream(stream_url: str, start_sec: int, clip_len: int) -> str:
    """
    Use ffmpeg to read directly from the remote audio stream URL and write a short WAV clip.
    Returns the path to the clip.
    """
    with _tempfile(suffix=".wav") as out_wav:
        # Build ffmpeg command; -ss before -i for fast seek; include reconnect flags for robustness
        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-loglevel", "error",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-ss", str(start_sec),
            "-i", stream_url,
            "-t", str(clip_len),
            "-vn",
            "-ac", "2",
            "-ar", "44100",
            out_wav,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not os.path.exists(out_wav) or os.path.getsize(out_wav) == 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed to extract clip")
        # Copy to a stable temp path we return (so context manager won't delete it yet)
        final_path = tempfile.mktemp(prefix="clip_", suffix=".wav")
        shutil.copy2(out_wav, final_path)
    return final_path

def _run_fpcalc_json(file_path: str) -> Dict[str, Any]:
    """
    Run Chromaprint fpcalc with JSON output; returns dict with 'duration' and 'fingerprint'.
    """
    try:
        proc = subprocess.run(
            ["fpcalc", "-json", file_path],
            capture_output=True, text=True, timeout=20
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "fpcalc failed")
        return json.loads(proc.stdout)
    except FileNotFoundError:
        raise RuntimeError("fpcalc (Chromaprint) not found on PATH.")
    except Exception as e:
        raise RuntimeError(f"Fingerprinting failed: {e}")

def _acoustid_lookup_best(fingerprint: str, duration: int) -> Optional[Dict[str, Any]]:
    """
    Query AcoustID; return best match dict: {artist, title, mbid, score}
    """
    if not ACOUSTID_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.acoustid.org/v2/lookup",
            params={
                "client": ACOUSTID_API_KEY,
                "meta": "recordings+recordingids+releasegroups+sources+artists",
                "fingerprint": fingerprint,
                "duration": int(duration),
            },
            timeout=8
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    best = None
    for res in data.get("results", []):
        res_score = float(res.get("score", 0.0))
        for rec in res.get("recordings", []):
            title = rec.get("title")
            mbid  = rec.get("id")
            artists = rec.get("artists", [])
            artist_name = (artists[0].get("name").strip() if artists else None)
            if artist_name and title:
                cand = {"artist": artist_name.strip(), "title": title.strip(), "mbid": mbid, "score": res_score}
                if (best is None) or (cand["score"] > best["score"]):
                    best = cand
    return best

async def identify_current_by_fingerprint(ctx, url: str, clip_len: int = FINGERPRINT_CLIP_SECONDS
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """
    Identify a YouTube track by reusing the current audio stream URL and fingerprinting a mid-song clip.
    Returns (artist, title, meta) or (None, None, None) if not confident.
    """
    # Get stream URL (no second yt-dlp download), and total duration if possible
    total_duration = None
    try:
        info = ytdl.extract_info(url, download=False)
        total_duration = info.get("duration")
    except Exception:
        total_duration = None

    stream_url = get_youtube_audio(url)
    if not stream_url:
        return None, None, None

    start_sec = _choose_fingerprint_window(total_duration, clip_len)

    await ctx.send(f"🔎 Identifying from audio…")
    clip_path = None
    try:
        clip_path = _extract_clip_from_stream(stream_url, start_sec, clip_len)
        fp = _run_fpcalc_json(clip_path)
        fingerprint = fp.get("fingerprint")
        duration = int(fp.get("duration", clip_len))
        if not fingerprint:
            return None, None, None
        best = _acoustid_lookup_best(fingerprint, duration)
        if not best:
            return None, None, None
        return best["artist"], best["title"], {"mbid": best.get("mbid"), "score": best.get("score")}
    except Exception as e:
        print(f"[identify] {e}")
        return None, None, None
    finally:
        if clip_path and os.path.exists(clip_path):
            try: os.remove(clip_path)
            except: pass

# ——— Music Queue ———
class MusicQueue:
    def __init__(self):
        self.queue = Queue()
        self.current = None
    async def add_to_queue(self, url):
        await self.queue.put(url)
    async def play_next(self, ctx):
        if self.queue.empty():
            self.current = None
            await ctx.send("Queue is empty.")
            return
        self.current = await self.queue.get()
        audio_url = get_youtube_audio(self.current)
        if not audio_url:
            await ctx.send(f"Error playing: {self.current}")
            return await self.play_next(ctx)
        ctx.voice_client.play(
            discord.FFmpegPCMAudio(audio_url, **ffmpeg_options),
            after=lambda e: bot.loop.call_soon_threadsafe(self.next_track, ctx)
        )
        await ctx.send(f"Now playing: {self.current}")
    def next_track(self, ctx):
        bot.loop.create_task(self.play_next(ctx))

music_queue = MusicQueue()

# ——— Inactivity Disconnect ———
@tasks.loop(minutes=1)
async def inactivity_check():
    inactivity_check.counter += 1
    for g in bot.guilds:
        vc = g.voice_client
        if vc and not vc.is_playing() and music_queue.queue.empty():
            if inactivity_check.counter >= 60:
                await vc.disconnect()
                inactivity_check.counter = 0
                return
        else:
            inactivity_check.counter = 0
inactivity_check.counter = 0

@bot.event
async def on_ready():
    inactivity_check.start()

# ——— Auto-Join Helper ———
async def ensure_voice(ctx):
    if not ctx.voice_client:
        if not ctx.author.voice:
            await ctx.send("You need to be in a voice channel.")
            return False
        await ctx.author.voice.channel.connect()
    return True

# ——— Commands ———

@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="YT Bot Info",
        description="Available commands",
        color=discord.Color.blue()
    )
    embed.add_field(name="!join", value="Make the bot join your voice channel.", inline=False)
    embed.add_field(name="!leave", value="Disconnect the bot from the voice channel.", inline=False)
    embed.add_field(name="!play <query or URL>", value="Search YouTube (or play a URL) and enqueue/play it.", inline=False)
    embed.add_field(name="!skip", value="Skip the current track.", inline=False)
    embed.add_field(name="!creed", value="Pick a random Creed song and play it.", inline=False)
    embed.add_field(
        name="!playlist [limit] [Artist - Track]", 
        value=(
            "Get up to `limit` (default 5, max 20) similar tracks via Last.fm,\n"
            "vote via reactions, and enqueue the winner.\n"
            "You can manually override artist/track: `!playlist 5 Adele - Hello`"
        ),
        inline=False
    )
    # (Optional) You can add a help line for !identify if you want.
    await ctx.send(embed=embed)


@bot.command()
async def play(ctx, *, query: str):
    if not await ensure_voice(ctx):
        return
    async with ctx.typing():
        if is_youtube_url(query):
            url = query
        else:
            url = search_youtube(query)
            if not url:
                return await ctx.send(f'No results for "{query}".')
        if ctx.voice_client.is_playing():
            await music_queue.add_to_queue(url)
            await ctx.send(f'Added to queue: {url}')
        else:
            await music_queue.add_to_queue(url)
            await music_queue.play_next(ctx)

@bot.command()
async def skip(ctx):
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await ctx.send("Skipped.")
    else:
        await ctx.send("Nothing is playing.")

@bot.command()
async def join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()

@bot.command()
async def creed(ctx):
    if not await ensure_voice(ctx):
        return
    try:
        with open('creed.txt') as f:
            songs = [l.strip() for l in f if l.strip()]
        if not songs:
            return await ctx.send("creed.txt is empty.")
        choice = random.choice(songs)
        await ctx.send(f'Random Creed pick: **{choice}**')
        async with ctx.typing():
            url = search_youtube(choice)
            if not url:
                return await ctx.send(f'No results for "{choice}".')
            if ctx.voice_client.is_playing():
                await music_queue.add_to_queue(url)
                await ctx.send(f'Added to queue: {choice}')
            else:
                await music_queue.add_to_queue(url)
                await music_queue.play_next(ctx)
    except FileNotFoundError:
        await ctx.send("creed.txt not found.")

# NEW: handy identify command (optional but useful for debugging/UX)
@bot.command()
async def identify(ctx):
    """
    Identify the currently playing YouTube track via audio fingerprinting (mid-song clip).
    """
    if not music_queue.current:
        return await ctx.send("No track is currently playing.")
    artist, title, meta = await identify_current_by_fingerprint(ctx, music_queue.current)
    if artist and title:
        conf = f" (score {meta['score']:.2f})" if meta and meta.get("score") is not None else ""
        await ctx.send(f"✅ Detected: **{artist} – {title}**{conf}")
    else:
        await ctx.send("❌ Couldn't confidently identify this track.")

@bot.command()
async def playlist(ctx, *args):
    """
    !playlist [limit] [Artist - Track]
    - limit: how many options to fetch (1–20, default 5)
    - Artist - Track: optional manual override if your YouTube title
      doesn’t split cleanly
    """
    # 1) Parse an optional numeric limit
    limit = 5
    arglist = list(args)
    if arglist and arglist[0].isdigit():
        limit = max(1, min(20, int(arglist.pop(0))))

    # 2) Ensure we're in voice and have a current track
    if not await ensure_voice(ctx):
        return
    if not music_queue.current:
        return await ctx.send("No track is currently playing.")

    # 3) Determine artist & track, with manual override → title parse → fingerprint fallback (prefers fingerprint)
    manual = " ".join(arglist).strip()
    artist = track = None

    if manual:
        if " - " not in manual:
            return await ctx.send("If you supply artist/track manually, use `Artist - Track` format.")
        artist, track = manual.split(" - ", 1)
        artist, track = artist.strip(), track.strip()
    else:
        # Try to parse from YouTube title first
        try:
            info       = ytdl.extract_info(music_queue.current, download=False)
            full_title = normalize_title_noise(info.get("title", "") or "")
        except Exception:
            full_title = ""
        a_guess, t_guess = split_artist_track(full_title)

        # Prefer accurate path: fingerprint a mid-song clip from the *current stream url*
        fp_artist = fp_title = None
        fp_meta = None
        fp_artist, fp_title, fp_meta = await identify_current_by_fingerprint(ctx, music_queue.current)

        # Use fingerprint if confident or if title parse is weak
        if fp_artist and fp_title and (not a_guess or not t_guess or (fp_meta and fp_meta.get("score", 0) >= 0.5)):
            artist, track = fp_artist, fp_title
        else:
            artist, track = a_guess, t_guess

        if not track:
            return await ctx.send(
                "Couldn't parse or identify artist/track. "
                "You can retry with `!playlist Artist - Track`."
            )

    # 4) Fetch similar tracks via Last.fm
    recs = get_similar_lastfm(artist, track, limit=limit)
    if not recs:
        return await ctx.send(f"No recommendations found for **{artist} – {track}**.")

    # 5) Build emoji list (1–10 then A–J) up to `limit`
    number_emojis = ['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
    letter_emojis = ['🇦','🇧','🇨','🇩','🇪','🇫','🇬','🇭','🇮','🇯']
    emojis = number_emojis + letter_emojis

    choices = recs[:limit]
    desc = "\n".join(
        f"{emojis[i]} **{c['artist']} – {c['name']}**"
        for i, c in enumerate(choices)
    )
    embed = discord.Embed(title="Vote for the next track:", description=desc)
    msg = await ctx.send(embed=embed)
    for i in range(len(choices)):
        await msg.add_reaction(emojis[i])

    # 6) Wait 20s, tally votes
    await asyncio.sleep(20)
    msg = await ctx.fetch_message(msg.id)
    votes = []
    for i in range(len(choices)):
        react = discord.utils.get(msg.reactions, emoji=emojis[i])
        count = (react.count - 1) if react else 0
        votes.append((count, i))

    max_votes, idx = max(votes, key=lambda x: x[0])
    if max_votes == 0:
        return await ctx.send("No votes were cast — cancelled.")

    # 7) Enqueue or play the winner
    winner = choices[idx]
    query  = f"{winner['artist']} - {winner['name']}"
    url    = search_youtube(query)
    if not url:
        return await ctx.send(f"Couldn't find YouTube for “{query}.”")

    if ctx.voice_client.is_playing():
        await music_queue.add_to_queue(url)
        await ctx.send(f'Added to queue: **{winner["artist"]} – {winner["name"]}**')
    else:
        await music_queue.add_to_queue(url)
        await music_queue.play_next(ctx)

@bot.command()
async def queue(ctx):
    """
    Display the currently playing track and up to  its queued songs.
    """
    # If nothing is playing and the queue is empty
    if not music_queue.current and music_queue.queue.empty():
        return await ctx.send("The queue is currently empty.")

    embed = discord.Embed(title="🎶 Current Queue", color=discord.Color.green())

    # Now playing
    if music_queue.current:
        try:
            info = ytdl.extract_info(music_queue.current, download=False)
            now_title = info.get("title", music_queue.current)
        except Exception:
            now_title = music_queue.current
        embed.add_field(name="▶ Now Playing", value=now_title, inline=False)

    # Upcoming tracks
    upcoming = list(music_queue.queue._queue)  # peek at asyncio.Queue
    if upcoming:
        desc = ""
        for idx, url in enumerate(upcoming, start=1):
            try:
                info = ytdl.extract_info(url, download=False)
                title = info.get("title", url)
            except Exception:
                title = url
            desc += f"**{idx}.** {title}\n"
        embed.add_field(name="⏭ Up Next", value=desc, inline=False)
    else:
        embed.add_field(name="⏭ Up Next", value="*(no songs queued)*", inline=False)

    await ctx.send(embed=embed)



# Run the bot
<<<<<<< HEAD:app.py
bot.run(os.getenv("DISCORD_TOKEN"))
=======
bot.run(get_token())


>>>>>>> 09901dcb989595e637a3108c9b823d17e03f968f:src/bot.py
