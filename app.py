import discord
from discord.ext import commands, tasks
import yt_dlp as youtube_dl
from asyncio import Queue
import re
import random
import asyncio
import requests
import os

# ——— CONFIG ———
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")

# ——— Bot & FFmpeg/YT‑DLP setup ———
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

def get_similar_lastfm(artist: str, track: str, limit: int = 5):
    """
    Call Last.fm track.getSimilar to return up to `limit` similar tracks.
    Returns list of dicts: {'artist': artist, 'name': track_name}.
    """
    print("Artist:" + artist, "track: "+track)
    resp = requests.get(
        "http://ws.audioscrobbler.com/2.0/",
        params={
            'method': 'track.getSimilar',
            'artist': artist,
            'track': track,
            'api_key': LASTFM_API_KEY,
            'format': 'json',
            'limit': limit
        },
        timeout=5
    )
    data = resp.json()

    entries = data.get("similartracks", {}).get("track", [])
    results = []
    for t in entries:
        name = t.get("name")
        art  = t.get("artist", {}).get("name")
        if name and art:
            results.append({'artist': art, 'name': name})
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

# ——— Auto‑Join Helper ———
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

    # 3) Determine artist & track, with manual override
    manual = " ".join(arglist).strip()
    if manual:
        if " - " not in manual:
            return await ctx.send("If you supply artist/track manually, use `Artist - Track` format.")
        artist, track = manual.split(" - ", 1)
        artist, track = artist.strip(), track.strip()
    else:
        # Extract from the YouTube title
        info       = ytdl.extract_info(music_queue.current, download=False)
        full_title = info.get("title", "")
        artist, track = split_artist_track(full_title)
        if not track:
            return await ctx.send(
                "Couldn't parse artist and track from the title.  "
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
bot.run(os.getenv("DISCORD_TOKEN"))


